#!/usr/bin/env python
import copy
import logging
import os
import stat
import subprocess
import sys
import tempfile
import time
import yaml

CONF_BACKUP_LIST = "home-backup-list.yaml"
CONF_BACKUP_CONF_TEMPLATE_FILE = "flexbackup.conf.tmpl"
CONF_TEMPFILE_PREFIX = "flexbackup-"
CONF_SUBDIR_EXCLUDE_LIST = ["lost+found"]
CONF_BACKUP_EXEC = "flexbackup"
CONF_BACKUP_EXEC_EXTRA_ARGS = []
CONF_TIER_LEVELING = 2
CONF_LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

class BackupManager:
    @staticmethod
    def flatten(fatlist):
        return [item for sublist in fatlist for item in sublist]

    @staticmethod
    def open_file(fn):
        try:
            f = open(fn, "r")
            return f.read()
        except OSError as err:
            logging.error("Error opening file %s: %s" % (fn, err))
            sys.exit(1)

    def __init__(self, conf, logger, dry_run):
        self.root_dir = conf['root_directory']
        self.backup_dest = conf['dest_directory']
        self.tmpfiles = {}
        self.tmpfiles["conf"] = ""
        self.conf_template = ""
        self.log = logger

        self.tier1 = conf['backup_tiers']['tier1']
        self.tier2 = conf['backup_tiers']['tier2']
        self.subdir_expansions = conf['subdirectory_expansions']
        self.dry_run = "-n" if dry_run else ""

        self.backup_cycle_listing = self.get_backup_cycle_listing()
        self.backup_cycle_index = self.get_cur_cycle_index(len(self.backup_cycle_listing))

        self.gen_temp_exec('gzip', 'exec /usr/bin/env pigz -p 10 -f "$@"')
        self.gen_temp_exec('tar', 'exec /usr/bin/env nocache /bin/tar "$@"')

    def err(self, err_msg=""):
        if err_msg:
            self.log.error(err_msg)
            sys.exit(1)

    def get_backup_cycle_listing(self):
        # a: tier1
        # b: tier2
        # a1, a2, a3, b1, a1, b2, a2, b3, a3, a1, a2, a3, b1, ....
        # |---------------------------------|
        #              one cycle
        #
        listing = copy.copy(self.tier1)
        for t1, t2 in zip(self.tier2, self.tier1):
            listing += [t1, t2]

        # collect remaining list items
        zipped_len = min(len(self.tier1), len(self.tier2))
        listing += self.tier1[zipped_len:]
        listing += self.tier2[zipped_len:]
        # assert(len(listing) == len(self.tier1) * 2 + len(self.tier2))
        return listing

    def get_inc_backup_set(self):
        inc_set = copy.copy(self.tier1)
        if self.backup_cycle_index % 2 == 0:
            inc_set += self.tier2

        full_backup_set = self.get_full_backup_set()
        for bset in full_backup_set:
            inc_set.remove(bset)

        return inc_set

    def get_full_backup_set(self):
        return [self.backup_cycle_listing[self.backup_cycle_index]]

    def get_cur_cycle_index(self, cycle_len):
        day_since_epoch = int(time.time()) / 86400
        return day_since_epoch % cycle_len

    def get_directory_listing(self, bdir):
        if bdir not in self.subdir_expansions:
            self.err("Backup set {} not found!".format(bdir))

        path = os.path.join(self.root_dir, bdir)
        if self.subdir_expansions[bdir]:
            # expand one level of sub-directories
            listing = [os.path.join(path, d) for d in os.listdir(path) if not d
                       in CONF_SUBDIR_EXCLUDE_LIST]
            return [d for d in listing if os.path.isdir(d)]
        return [path]

    def gen_set_name(self, bset):
        return "___".join(bset)

    def gen_conf(self, bset):
        if not self.conf_template:
            self.conf_template = self.open_file(CONF_BACKUP_CONF_TEMPLATE_FILE)
            self.tmpfiles["conf"] = tempfile.mkstemp(prefix=CONF_TEMPFILE_PREFIX)[1]

        # Each set has multiple directories
        bdirs = [" ".join(self.get_directory_listing(bdir)) for bdir in bset]
        conf_str = self.conf_template
        for l, r in (("@@SET_NAME@@", self.gen_set_name(bset)),
                     ("@@SET_CONTENT@@", " ".join(bdirs)),
                     ("@@BACKUP_STORE_DIR@@", self.backup_dest),
                     ("@@GZIP@@", self.tmpfiles['gzip']),
                     ("@@TAR@@", self.tmpfiles['tar'])):
            conf_str = conf_str.replace(l, r)

        # Write main conf
        f = open(self.tmpfiles["conf"], "w")
        f.write(conf_str)
        f.close()

    def clean_tmpfiles(self):
        for (_, tmpf) in self.tmpfiles.items():
            try:
                if tmpf and os.path.isfile(tmpf):
                    os.unlink(tmpf)
            except OSError as err:
                self.log.warning("Error removing file %s: %s" % (tmpf, err))

    def gen_temp_exec(self, bin_name, bin_content):
        assert type(bin_name) == str
        tmpf = tempfile.mkstemp(prefix=CONF_TEMPFILE_PREFIX + bin_name)[1]

        f = open(tmpf, "w")
        f.write("#!/bin/sh\n")
        f.write(bin_content + "\n")
        f.close()

        os.chmod(tmpf, os.stat(tmpf).st_mode | stat.S_IEXEC)
        self.tmpfiles[bin_name] = tmpf

    def do_run_backup_prog(self, bset, level):
        assert level == "full" or level == "incremental"
        cmd = [
            CONF_BACKUP_EXEC,
            self.dry_run,
            "-c", self.tmpfiles["conf"],
            "-level", level,
            "-set", self.gen_set_name(bset),
            ] + CONF_BACKUP_EXEC_EXTRA_ARGS

        try:
            self.log.info("Executing command: %s" % " ".join(cmd))
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            # Poll process output until it stops
            while process.poll() is None:
                self.log.info(process.stdout.read().decode())
                time.sleep(0.5)
        except subprocess.CalledProcessError as e:
            self.log.error(e.output)

    def do_run_backup(self, bset_list, level):
        for bset in bset_list:
            self.gen_conf(bset)
            self.do_run_backup_prog(bset, level)

    def do_backup_inc(self):
        self.do_run_backup(self.get_inc_backup_set(), "incremental")

    def do_backup_full(self):
        self.do_run_backup(self.get_full_backup_set(), "full")

    def do_backup_summary(self):
        full = self.get_full_backup_set()
        inc = self.get_inc_backup_set()
        self.log.info("Incremental backup:\t" + ", ".join(self.flatten(inc)))
        self.log.info("Full backup:\t" + ", ".join(self.flatten(full)))

    def do_backup(self):
        import atexit
        atexit.register(self.clean_tmpfile)

        self.log.debug("Running cyclic backup at index (%s/%s)",
                       self.backup_cycle_index, len(self.backup_cycle_listing))
        self.do_backup_summary()
        self.do_backup_inc()
        self.do_backup_full()

def load_yaml(fn):
    try:
        txt = BackupManager.open_file(fn)
        return yaml.load(txt, yaml.SafeLoader)
    except yaml.YAMLError as exc:
        print("Error while parsing YAML file: %s" % fn)
        if hasattr(exc, 'problem_mark'):
            logging.error("YAML error\n:{}\n{} {}".format(
                str(exc.problem_mark),
                str(exc.problem),
                str(exc.context) if exc.context else ""))
        sys.exit(1)

def main():
    logging.basicConfig(level=logging.DEBUG, format=CONF_LOG_FORMAT)
    logger = logging.getLogger("backup")
    conf = load_yaml(CONF_BACKUP_LIST)
    backup_manager = BackupManager(conf, logger, dry_run=True)
    backup_manager.do_backup()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Stopping %s due to keyboard interrupt ..." % sys.argv[0])
