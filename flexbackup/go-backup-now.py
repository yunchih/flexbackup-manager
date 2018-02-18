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

#
# WSLAB Flexbackup Wrapper Script
#
# Introduction
#   This script is a backup scheduler built upon flexbackup
#   that manages backup set according to their tiers and
#   corresponding SLAs.  We currently support two tiers with
#   different SLAs.
#
# The tier with higher SLA will have the following properties:
#     1. More frequent full backup.
#     2. More dated full/incremental backup stored.
#     3. More frequent incremental backup.
#
# The incentives of such distinction are as followed:
#     1. When there's data loss, the dataset can be recovered faster.
#     2. Make it possible for user to recover very old snapshot of their
#        data.
#     3. The data is being actively used, thus daily incremental
#        backup can effectively catch daily delta.  On the other hand,
#        Tier 2 data is rarely updated, thus frequent incremental backup
#        can be a waste.
#
# Functionality
#   1. Backup set is read from a yaml file, whose format is expected to be:
#
#      root_directory: "/e"            # The directory where original data is stored
#      dest_directory: "/backup/nfs"   # The directory where backup data is stored
#      subdirectory_expansions:        # Whether or not to backup subdirectory separatedly
#          A: true
#          B: true
#          C: false
#      incremental_backup_frequency:
#          tier1: 1 # daily
#          tier2: 3 # once every 3 day
#      backup_tiers:                   # The names of data
#          tier1:
#              - - A
#                - B
#          tier2:
#              - - C
#
#      In the above example, /e/A, /e/B will be backuped in the same day in Tier 1,
#      /e/C will be backuped individually in Tier 2.
#
#   2. Incremental Backup:
#      Tier N has daily incremental backup, while Tier 2 is once every M day, where
#      N < M.
#
#   3. Full backup:
#      Full backup is scheduled statically according to "backup cycle".
#      A backup cycle consists of two tier1 and one tier2.
#      tier1 runs first, then followed by interception of
#      tier2 and the other tier1.  Here's an example full
#      cycle:
#
#       a: tier1
#       b: tier2
#       a1, a2, a3, b1, a1, b2, a2, b3, a3, a1, a2, a3, b1, ....
#       |---------------------------------|
#                    one cycle
#
#      Their mean-time-between-backup are:
#
#          tier1: len(tier1) + len(tier2)/2
#          tier2: len(tier1)*2 + len(tier2)
#
#      A longer mean-time-between-backup means longer
#      recovery time.
#
#   3. Backup retention period:
#      Tier 1 keeps two set of full/incremental backup data, while Tier 2 keeps one.
#
#

CONF_BACKUP_LIST = "home-backup-list.yaml"
CONF_BACKUP_CONF_TEMPLATE_FILE = "flexbackup.conf.tmpl"
CONF_TEMPFILE_PREFIX = "flexbackup-"
CONF_SUBDIR_EXCLUDE_LIST = ["lost+found"]
CONF_BACKUP_EXEC = "flexbackup"
CONF_BACKUP_EXEC_EXTRA_ARGS = []
CONF_LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

class BackupManager:
    """ The backup manager class """

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

        # Generate shell script wrappers of pigz and tar so we can add
        # some parameters we want

        # Don't let pigz consume all CPUs, just part of them
        self.gen_temp_exec('gzip', 'exec /usr/bin/env pigz -p 10 -f "$@"')
        # Try using 'nocache' to prevent cache pollution
        self.gen_temp_exec('tar', 'exec /usr/bin/env nocache -n 2 /bin/tar "$@"')

    def err(self, err_msg=""):
        """ Generate error message and exit """
        if err_msg:
            self.log.error(err_msg)
            sys.exit(1)

    def get_backup_cycle_listing(self):
        """
        A backup cycle consists of two tier1 and one tier2.
        tier1 runs first, then followed by interception of
        tier2 and the other tier1.  Here's an example full
        cycle:

         a: tier1
         b: tier2
         a1, a2, a3, b1, a1, b2, a2, b3, a3, a1, a2, a3, b1, ....
         |---------------------------------|
                      one cycle

        Their mean-of-between-backup time are:

            tier1: len(tier1) + len(tier2)/2
            tier2: len(tier1)*2 + len(tier2)

    def get_backup_cycle_listing(self):
        """
        Generate a backup cycle (see illustration in the head of the file.
        """

        listing = copy.copy(self.tier1)
        for t1, t2 in zip(self.tier2, self.tier1):
            listing += [t1, t2]

        # collect remaining list items
        zipped_len = min(len(self.tier1), len(self.tier2))
        listing += self.tier1[zipped_len:]
        listing += self.tier2[zipped_len:]
        # assert len(listing) == len(self.tier1) * 2 + len(self.tier2)
        return listing

    def get_inc_backup_set(self):
        """
        We backup tier1 everyday, tier2 once every 2 days

        returns: A list of backup targets
        """
        inc_set = copy.copy(self.tier1)
        if self.backup_cycle_index % 2 == 0:
            inc_set += self.tier2

        # Don't do incremental backup on today's full backup set
        full_backup_set = self.get_full_backup_set()
        for bset in full_backup_set:
            inc_set.remove(bset)

        return inc_set

    def get_full_backup_set(self):
        return [self.backup_cycle_listing[self.backup_cycle_index]]

    def get_cur_cycle_index(self, cycle_len):
        """
        Use current day since epoch to determine
        which position we're in in the backup cycle.
        """
        day_since_epoch = int(time.time()) / 86400
        return day_since_epoch % cycle_len

    def get_directory_listing(self, bdir):
        """
        Get the first subdirectories of target directory.
        The purpose is to expand a backup set into multiple
        subset so admin can find them faster.
        """
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
        """
        Generate our own flexbackup.conf
        """
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
        """ Remove all the temporary file we have created. """
        for (_, tmpf) in self.tmpfiles.items():
            try:
                if tmpf and os.path.isfile(tmpf):
                    os.unlink(tmpf)
            except OSError as err:
                self.log.warning("Error removing file %s: %s" % (tmpf, err))

    def gen_temp_exec(self, bin_name, bin_content):
        """
        Generate a temporary shell script for the main backup program.
        """
        assert isinstance(bin_name, str)
        tmpf = tempfile.mkstemp(prefix=CONF_TEMPFILE_PREFIX + bin_name)[1]

        f = open(tmpf, "w")
        f.write("#!/bin/sh\n")
        f.write(bin_content + "\n")
        f.close()

        os.chmod(tmpf, os.stat(tmpf).st_mode | stat.S_IEXEC)
        self.tmpfiles[bin_name] = tmpf

    def do_run_backup_prog(self, bset, level):
        """ Run the target backup program """
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
        """ Loop through today's backup set """
        for bset in bset_list:
            self.gen_conf(bset)
            self.do_run_backup_prog(bset, level)

    def do_backup_inc(self):
        """ Full backup """
        self.do_run_backup(self.get_inc_backup_set(), "incremental")

    def do_backup_full(self):
        """ Full backup """
        self.do_run_backup(self.get_full_backup_set(), "full")

    def do_backup_summary(self):
        """ Summarize what will be backuped """
        full = self.get_full_backup_set()
        inc = self.get_inc_backup_set()
        self.log.info("Incremental backup:\t" + ", ".join(self.flatten(inc)))
        self.log.info("Full backup:\t" + ", ".join(self.flatten(full)))

    def do_backup_gc(self):
        """ Delete stale backups """
        # TODO

    def do_backup(self):
        """ Backup main method """

        # Clean generated temporary files when the script stops
        import atexit
        atexit.register(self.clean_tmpfiles)

        self.log.debug("Running cyclic backup at index (%s/%s)",
                       self.backup_cycle_index, len(self.backup_cycle_listing))
        self.do_backup_summary()
        self.do_backup_inc()
        self.do_backup_full()

def load_yaml(fn):
    """ Load and read an yaml file """
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
