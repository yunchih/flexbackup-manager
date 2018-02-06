#/usr/bin/env python3

import os
import subprocess
import sys
import tempfile
import time
import yaml

CONF_BACKUP_LIST = "home-backup-list.yaml"
CONF_BACKUP_CONF_TEMPLATE_FILE = "backup.conf.tmpl"
CONF_BACKUP_MAIN_CONF_TEMPLATE_FILE = "backupninja.conf.tmpl"
CONF_REQUIRED_HEADER = ["backup_list", "backup_remote"]
CONF_REMOTE_FIELDS = ["host",  "port", "user", "password", "dest_base"]
CONF_TEMPFILE_PREFIX = "ws-ninja-backup-"
CONF_BACKUP_EXEC = "backupninja"
CONF_BACKUP_EXEC_EXTRA_ARGS = ["--now", "--test"]

def open_file(fn):
    try:
        f = open(fn, "r")
        return f.read()
    except:
        print("Error opening file: %s" % fn)
        sys.exit(1)

def load_yaml(fn):
    try:
        txt = open_file(fn)
        return yaml.load(txt, yaml.SafeLoader)
    except yaml.YAMLError as exc:
        print("Error while parsing YAML file: %s" % f)
        if hasattr(exc, 'problem_mark'):
            print("YAML error\n:{}\n{} {}".format(
                    str(exc.problem_mark),
                    str(exc.problem),
                    str(exc.context) if exc.context else ""))
        sys.exit(1)

class BackupManager:
    def __init__(self, conf):
        self.conf_check_required_fields(conf)
        self.conf_check_remote(conf)

        self.backup_list = conf["backup_list"]
        self.remote = conf["backup_remote"]
        self.rotation_cycle = len(self.backup_list)
        self.backup_target_index = self.get_current_rotation_index()
        self.backup_target = self.backup_list[self.backup_target_index]

    def get_current_rotation_index(self):
        day_since_epoch = int(time.time()) / 86400
        return day_since_epoch % self.rotation_cycle

    def conf_check_required_fields(self, conf):
        for h in CONF_REQUIRED_HEADER:
            if not h in conf:
                print("Field %s missing in configuration file" % h)
                sys.exit(1)

    def conf_check_remote(self, conf):
        t = conf["backup_remote"]
        for field in CONF_REMOTE_FIELDS:
            if not field in t:
                print("Field backup_remote.%s required" % field)
                sys.exit(1)

    def gen_backup_confs(self):
        main_conf = open_file(CONF_BACKUP_MAIN_CONF_TEMPLATE_FILE)
        tmpl_conf = open_file(CONF_BACKUP_CONF_TEMPLATE_FILE)
        tmpdir = tempfile.mkdtemp(prefix=CONF_TEMPFILE_PREFIX)

        # Write main conf
        self.backup_main_conf = tempfile.mkstemp(prefix=CONF_TEMPFILE_PREFIX)[1]
        f = open(self.backup_main_conf, "w")
        f.write(main_conf.format(CONFDIR=tmpdir))
        f.close()

        # Write each backup target conf
        confs = []
        for fs in self.backup_target:
            fs_base = os.path.basename(fs)
            tmp = tempfile.mkstemp(prefix=fs_base, dir=tmpdir)[1]
            f = open(tmp, "w")
            f.write(tmpl_conf.format(
                SRC = fs,
                USER = self.remote["user"],
                HOST = self.remote["host"],
                PORT = self.remote["port"],
                DEST = os.path.join(self.remote["dest_base"], fs_base),
                PASS = self.remote["password"]))
            f.close()
            confs.append(tmp)

        self.backup_tmpdir = tmpdir
        self.backup_confs = confs

    def clean_backup_confs(self):
        for conf in self.backup_confs:
            os.unlink(conf)
        os.rmdir(self.backup_tmpdir)
        os.unlink(self.backup_main_conf)

    def do_backup(self):
        self.gen_backup_confs()

        import atexit
        atexit.register(self.clean_backup_confs)

        # Do backup
        try:
            cmd = [CONF_BACKUP_EXEC, "-f", self.backup_main_conf] + CONF_BACKUP_EXEC_EXTRA_ARGS
            print("Running command: %s" % " ".join(cmd))
            out = subprocess.check_output(cmd)
        except subprocess.CalledProcessError as e:
                print(e.output)


if __name__ == "__main__":
    conf = load_yaml(CONF_BACKUP_LIST)
    backup_manager = BackupManager(conf)
    backup_manager.do_backup()
