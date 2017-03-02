#!/usr/bin/python
#-*- coding: utf-8 -*-
 
# Name:     PostgreSQL backup script (designed for Odoo)
# Author:   Ryan Cole (https://ryanc.me/)
#
# Description:      
#   Simple comand-line script to handle creation/restoration of database backups,
#   archiving to offsite locations, and auto-cleanup of old backups.
#
# Requires:
#   - python 2.7.*
#   - python-docopt
#   - AWS CLI

#TODO: move to direct API calls for pg_dump, pg_restore, and AWS-related functions.
#TODO: `list`, `push`, `pull` and `cleanup` functions
#TODO: exception handling (datetime, subprocess, etc)
#TODO: `--mask` switch for setting local file permissions
#TODO: `--dry` switch for testing
#TODO: fileserver/SCP integration
#TODO: support for gpg encryption
#TODO: string-matching for command options (e.g. `command --switch --input=<filename>` would auto-fill the filename)

conf = {
    # default local working directory for database dumps
    "--dir":      "/opt/odoo-backups/",

    # default postgresql connection info
    # for password-authentication
    "-u":       "username",
    "-w":       "password",
    "-h":       "host",
    "-p":       "port",

    # enable for peer-authentication - this requires that the script is run as the Odoo user
    "--peer":    False,

    # default remote fileserver ssh info
    # note: currently not implemented
    "--fsuser":   "username",
    "--fskey":    "keyfile",
    "--fshost":   "",
    "--fsport":   "22",

    # default aws s3 bucket
    "--bucket":     "my_bucket_name",

    # override this to use a different profile for aws cli auth
    # run `aws configure --profile=<name>`
    "--profile":    "<name>",

    # default recipient name for gpg encryption
    # this 'name' corresponds to a public key
    # in the current users' gpg keychain
    "--gpgname":    "admin@mydomain.com",

    # format for timestamps in backup filenames (e.g. <database-name>__<--savefmt>.pgdump.gz.gpg)
    # warning: the script uses double-underscores (e.g. `__`) to separate the database-name and 
    #          timestamp in the filename. using __ in the savefmt will probably break things.
    "--savefmt": "%Y-%m-%d_%H-%M-%S",

    # format for the `--date=xx` arg
    "--datefmt": "%d/%m/%Y",
#    "--datefmt": "%m/%d/%Y",
    
    # logfile location (must be writeable)
    "--logfile":          "/var/log/zorb-backup.log",

    # archive commands
    # options are appended
    "gzipCommand":      "gzip",
    "gzipOptions":      "-9 --force",
    "gunzipCommand":    "gunzip",
    "gunzipOptions":    "--force",

    # database dump/restore/create commands
    # defaults here are tailored for Odoo,
    # but should work fine for general backups
    "dumpCommand":      "pg_dump",
    "dumpOptions":      "-E UTF-8 -F p -b",
    "restoreCommand":   "psql",
    "restoreOptions":   "",
    "createCommand":    "createdb",
    "createOptions":    "",

    # encryption/decryption commands and options
    "encryptCommand":   "gpg",
    "encryptOptions":   "--no-use-agent --quiet --no-tty --batch --yes --cipher-algo AES256",
    "decryptCommand":   "gpg",
    "decryptOptions":   "--no-use-agent --quiet --yes",

    # cleanup old backups every time the script runs?
    "alwaysCleanup":    False,
}


doc = """PostgreSQL Backups Script

Usage:
    pgback.py create (s3 | fileserver | local | all) <source-db> [options]
    pgback.py restore (local | s3 | fileserver) <source-db-name> <dest-db> [--name=<filename> | --date=<date>] [options]
    pgback.py cleanup (s3 | fileserver | local | all) <maxage-days> [db-name] [--archive=(s3 | fileserver)] [options]
    pgback.py push (s3 | fileserver | all) <dbname> [--name=<filename> | --date=<date>] [options]
    pgback.py pull (s3 | fileserver) <dbname> [--name=<filename> | --date=<date>] [options]
    pgback.py list (s3 | fileserver | local | all) [dbname]
    pgback.py (-h | --help | --version)

Note: 
    Options are read from the config table by default, but can be overriden with command-line arguments/switches.

    See the `config = {}` section of this file for more details.

Examples:
    Create a backup of `livedb` using peer authentication, and push it to s3://my.backups.bucket
    > pgback.py create s3 livedb -u pgusername --peer --bucket my.backups.bucket

    Create a backup of `livedb` using password authentication, and push it to a remote fileserver using scp
    > pgback.py create fileserver livedb -u odoo -w password --fsinfo username:password@backups.example.com:/home/backups/livedb --fsport 22

    Restore a backup of `backupdb` to a new database called `newdb`. Search s3://my.backups.bucket for the latest backup from 24/08/2016
    > pgback.py restore s3 backupdb newdb --date 24/08/2016

    Restore a backup of `backupdb`. --date and --name are omitted, so the script will find the most recent backup matching `backupdb`
    > pgback.py restore local backupdb newdb --dir /home/backups/livedb/

    TODO: more examples

Options:
    --date=<date>       search for files whose date matches dd/mm/yyyy in the servers' time
                        pulls most recent backup from selected day if multiple matches are found
    --name=<name>       search for literal filename match

    -u <username>       database username
    -w <password>       database password (not available when using --peer)
    -h <host>           database host     (not available when using --peer)
    -p <port>           database port
    --peer              use peer authentication for database

    --bucket <bucket>   bucket to use for s3 uploads/downloads
    --profile <profile> profile to use for aws cli auth (see `aws configure help`)

    --gpgname <name>    name to use as recipient for gpg encryption
    --gpgpass <pass>    password for symmetric encryption with gpg (mutually exlusive with --gpgname)

    --fsinfo            standard ssh connection string (e.g. user:password@host:/folder)
    --fskey             ssh keyfile for fileserver
    --fsuser            user for fileserver
    --fshost            host for fileserver
    --fsport            port for fileserver
    --fspath            path to backups files on fileserver

    --dir <dir>         working directory for backup files (default is ./)
    --savefmt <format>  datetime format to use for backup filenames
    --datefmt <format>  datetime format to use for the '--date=' arg
    --su <user>         database dump/restore script as <user> (script must be run as root)
    --logfile <logfile> must be writeable by whoever is running the script

    -a, --all           list ALL backup files when using the `list` command
    -v, --verbose       enable extra-detailed output
    -s, --silent        disable all output (does NOT imply -x)
    -x, --noconfirm     disable yes/no confirmations for irreversible actions
                        such as database restores, or file deletions
    -z, --nozip         disable gzipping
"""


from docopt import docopt
from datetime import datetime
from os import path, devnull
import sys
import subprocess

# process args with docopt
args = docopt(doc, version="1.0.0")

# black hole for pesky information
devnull = open(devnull, "w")


# print to logfile
def log(t, message):
    dateString = datetime.now().strftime("%Y/%m/%d %I:%M:%S%p")
    string = dateString + "  " + t + ": " + message
    with open(conf["--logfile"], "a") as f:
        f.write(string + "\n")

# print to stdout
def say(message, sameline=False, silent=True):
    # no output with the -s or --silent switch
    if (arg("-s") or arg("--silent")) and silent:
        return

    if sameline:
        print message + "  ",
        sys.stdout.flush()
    else:
        print message

# helper function for running shell commands
def cmd(message, detail, cmd, stdout=None):
    if arg("-s") or arg("--silent"):
        stdout=devnull

    say(message, True)
    status = subprocess.call(cmd, shell=True, stdout=stdout)

    if status == 0:
        log("SUCCESS", message + detail)
        say("done")
    else:
        log("ERROR", message + detail)
        say("error!")
        sys.exit(1)

# check for argument presence or value
def arg(name, default=None):
    if name in args and args[name] != None and args[name] != False:
        return args[name]

    if default != None:
        return default

    if name in conf and conf[name] != None:
        return conf[name]

    return False

# yes/no prompt, return true/false respectively
def promptYesNo(message, default=False):
    if arg("-x") or arg("--noconfirm"):
        return True

    if default:
        prompt = " [Y/n]:"
    elif not default:
        prompt = " [y/N]:"
    else:
        prompt = " [y/n]:"

    say(message + prompt, sameline=True, silent=False)
    choice = raw_input().lower()
    if not default:
        if choice == "y":
            return True
        else:
            return False
    elif default:
        if choice == "n":
            return False
        else:
            return True
    else:
        if choice == "y":
            return True
        elif choice == "n":
            return False
        else:
            return None

    # clear buffer
    say("")

# parse backup filename to dbname, date
def parseFilename(filename):
    # first, ensure @filename is _just_ the file's name
    filename = path.basename(filename)

    # strip .gpg, .gz, and .pgdump
    if filename[-4:] == ".gpg":
        filename = filename[:-4]
    if filename[-3:] == ".gz":
        filename = filename[:-3]
    if filename[-7:] == ".pgdump":
        filename = filename[:-7]

    sep = filename.find("__")
    dbname = filename[0:sep]
    dbdate = datetime.strptime(filename[sep+2:], arg("--savefmt"))

    return dbname, dbdate

# find most recent backup in list of backups
def findNewest(backups):
    if len(backups) == 1:
        return backups[0]

    match = backups[0]

    for backup in backups:
        if backup[1] > match[1]:
            match = backup

    return match


# zip file and return new filename
def gzipFile(absFilename):
    if arg("-z") or arg("--nozip"):
        return absFilename

    opts = arg("gzipOptions")

    if arg("-x") or arg("--noconfirm"):
        opts = opts + " --force --quiet"

    cmd("Gzipping...  ", "", arg("gzipCommand") + " " + opts + " " + absFilename)

    return absFilename + ".gz"

# unzip and return new filename
def gunzipFile(absFilename):
    if not absFilename[-3:] == ".gz":
        return absFilename

    opts = arg("gunzipOptions")

    if arg("-x") or arg("--noconfirm"):
        opts = opts + " --force --quiet"

    cmd("Unzipping...  ", "", arg("gunzipCommand") + " " + opts + " " + absFilename)

    return absFilename[:-3]

# encrypt file with gpg
def encryptFile(absFilename, recipient=None, password=None):
    if not password and not recipient:
        return absFilename

    # keep user in-the-loop
    if password and recipient:
        say("Both --gpgpass and --gpgname were supplied, but they can not be used in combination.")
        say("Falling back to --gpgname...")
        log("INFO", "User supplied --gpgpass and --gpgname, falling back to --gpgname")

    if recipient:
        command = arg("encryptCommand") + " " + arg("encryptOptions") + " -o " + absFilename + ".gpg -r " + recipient + " -e " + absFilename
    else:
        command = arg("encryptCommand") + " " + arg("encryptOptions") + " -o " + absFilename + ".gpg --passphrase " + password + " -c " + absFilename
    
    cmd("Encrypting...  ", "", command)

    # cleanup the non-encrypted base file
    cmd("Cleaning up...  ", "", "rm -f " + absFilename)

    return absFilename + ".gpg"

# decrypt file with gpg
def decryptFile(filename):
    command = arg("decryptCommand") + " " + arg("decryptOptions") + " -o " + absFilename[:-4] + " -d " + absFilename

    cmd("Decrypting...  ", "", command)

    cmd("Cleaning up...  ", "", "rm -f " + filename)

    return filename[:-4]


# dump database using password auth
def dumpDatabasePassword(dbname, filename, username, password, host, port):
    # dump database
    command = arg("dumpCommand") + " --dbname=postgresql://" + username + ":" + password + "@" + host + ":" + port + "/" + dbname + " " + arg("dumpOptions") + " -f " + filename
    logstr = username + ":[password]@" + host + ":" + port  + "/" + dbname + "  ->  " + filename
    cmd("Dumping database...  ", logstr, command)

    # gzipped for extra $$
    return filename

# dump database using peer auth
def dumpDatabasePeer(dbname, filename, username, port):
    # dump database
    command = arg("dumpCommand") + " -d " + dbname + " -U " + username + " -p " + port + " " + arg("dumpOptions") + " -f " + filename
    logstr = username + ":[peer]@localhost:" + port + "/" + dbname + "  ->  " + filename
    cmd("Dumping database...  ", logstr, command)
    # gzipped for extra $$
    return filename

# restore database using password auth
def restoreDatabasePassword(dbname, filename, username, password, host, port):
    # check that the user really does want to do the thing...
    if not promptYesNo("Restore to `" + dbname + "` from `" + filename + "`?"):
        return

    # createdb doesn't accept postgresql:// URI's, so we need to export an env variable
    cmd("Setting PGPASSWORD...  ", "", "export PGPASSWORD=" + password)
    cmd("Creating database...  ", dbname, "createdb -h " + arg("-h") + " -p " + arg("-p") + " -U " + arg("-u") + " " + dbname)
    cmd("Clearing PGPASSWORD...  ", "", "unset PGPASSWORD")

    # restore
    command = arg("restoreCommand") + " --dbname=postgresql://" + username + ":" + password + "@" + host + ":" + port + "/" + dbname + " " + arg("restoreOptions") + " < " + filename
    cmd("Restoring database...  ", filename + " -> " + dbname, command)

# restore database using peer auth
def restoreDatabasePeer(dbname, filename, username, port):
    # see restoreDatabasePassword() for details
    if not promptYesNo("Restore to `" + dbname + "` from `" + filename + "`?"):
        return

    cmd("Creating database...  ", dbname, arg("createCommand") + " -U " + username + " -p " + port + " " + dbname + " " + arg("createOptions"))

    command = arg("restoreCommand") + " -U " + username + " -p " + port + " -d " + dbname + " " + arg("restoreOptions") + " < " + filename
    cmd("Restoring database...  ", filename + " -> " + dbname, command)



# upload to s3 from file
def uploadToS3(absFilename, bucket, profile):
    # get lonely filename
    _, filename = path.split(absFilename)

    command = "aws s3 cp " + absFilename + " s3://" + bucket + "/" + filename + " --only-show-errors --profile=" + profile
    cmd("Uploading to S3...  ", absFilename + " -> s3://" + bucket + "/", command)

# download from s3 to file
def downloadFromS3(bucket, folder, filename, profile):
    absFilename = path.abspath(folder) + "/"+ filename

    command = "aws s3 cp s3://" + bucket + "/" + filename + " " + absFilename + " --only-show-errors --profile=" + profile
    cmd("Downloading from S3...  ", bucket + " -> " + absFilename, command)

    return absFilename

# search on s3 for either date or literal string match
def searchOnS3(bucket, profile, sourceDbName, date=False, name=False):
    say("Searching S3...  ", True)

    # list the bucket contents
    command = "aws s3 ls s3://" + bucket + "/ --profile=" + profile
    res = subprocess.check_output(command, shell=True)
    
    log("Success", "Searching S3 for backup files")
    say("done")

    # this function should eventually match a single backup file
    match = None

    # parse the output of `aws s3 ls`
    backups = []
    for line in res.splitlines():
        # `aws s3 ls` returns data like:
        # <date> <time>    <size> <filename>
        filename = line.split(None, 3)[3]
        dbname, dbdate = parseFilename(filename)

        if dbname == sourceDbName:
            backups.append([dbname, dbdate, filename])

    # no matches :(
    if len(backups) < 1:
        log("ERROR", "Restore failed - no matching backups found")
        say("Searching S3 Failed! Couldn't find any matching backups")
        exit(1)

    # `--date=xx` was used
    if date:
        targetDate = datetime.strptime(date, arg("--datefmt")).date()

        matches = []
        for backup in backups: 
            if targetDate == backup[1].date():
                matches.append(backup)

        if len(matches) < 1:
            log("ERROR", "Searching S3 - Could not find a match for the date " + date)
            say("Searching S3 Failed! Couldn't find any files with date matching `" + date + "`")
            exit(1)

        match = findNewest(matches)

    # `--name=xx` was used
    elif name:
        # search by filename
        for backup in backups:
            if backup[0] == name:
                match = name
                break
    # user didn't specify date OR filename, search for absolute newest backup
    else:
        match = findNewest(backups)

    # no matches! </3
    if not match:
        log("ERROR", "Restore failed - No valid backups after dbname/date checking")
        say("Searching S3 Failed! Couldn't find any backups that matched your <source-db-name> or --date")
        exit(1)

    # yay
    log("Success", "Search S3 - Found matching file `" + match[2] + "`")
    return match[2]



# upload to fileserver from file
def uploadToServer():

    print("Fileserver upload/download is not implemented yet.")

# download from fileserver to file
def downloadFromServer():

    print("Fileserver upload/download is not implemented yet.")

# search on fileserver for either date or literal string match
def searchOnServer():

    print("Fileserver upload/download is not implemented yet.")


# search local folder
def searchLocal(directory, dbname, date=None, name=None):

    print("Local searching is not currently implemented, please use --name instead.")

# create a backup
if arg("create"):
    log("", "Starting a new backup-create job")

    # parse arguments
    dbUser = arg("-u")
    dbPass = arg("-w")
    dbHost = arg("-h")
    dbPort = arg("-p")
    dbName = args["<source-db>"]
 
    workdir = path.abspath(arg("--dir")).rstrip("/")
    if not path.isdir(workdir):
        say("Error! The path could not be found: `" + workdir + "`")
        log("ERROR", "The path could not be found    `" + workdir + "`")
        exit(1)

    # generate filename with timestamp
    dateString = datetime.now().strftime(arg("--savefmt"))
    filename = dbName + "__" + dateString + ".pgdump"
    absFilename = path.abspath(workdir + "/" + filename)

    # dump to .pgdump file
    if arg("--peer"):
        absFilename = dumpDatabasePeer(dbName, absFilename, dbUser, dbPort)
    else:
        absFilename = dumpDatabasePassword(dbName, absFilename, dbUser, dbPass, dbHost, dbPort)

    # gzip the file
    absFilename = gzipFile(absFilename)

    # encrypt the file
    absFilename = encryptFile(absFilename, arg("--gpgname"), arg("--gpgpass"))

    # upload to S3
    if arg("all") or arg("s3"):
        uploadToS3(absFilename, arg("--bucket"), arg("--profile"))
    
    # upload to fileserver
    if arg("all") or arg("fileserver"):
        print("TODO: ...me!")

# restore a backup
elif arg("restore"):
    log("", "Starting a new backup-restore job")
    # parse arguments
    dbUser = arg("-u")
    dbPass = arg("-w")
    dbHost = arg("-h")
    dbPort = arg("-p")
    dbName = args["<dest-db>"]
    dbSourceName = args["<source-db-name>"]

    workdir = path.abspath(arg("--dir")).rstrip("/")
    if not path.isdir(workdir):
        say("Error! The path could not be found: `" + workdir + "`")
        log("FATAL ERROR", "The path could not be found    `" + workdir + "`")
        exit(1)

    absFilename = False

    # restore from S3 bucket
    if arg("s3"):

        # check which match-type the user selected
        filename = searchOnS3(arg("--bucket"), arg("--profile"), dbSourceName, date=arg("--date"), name=arg("--name"))

        # found a backup, prompt for confirmation
        say("Found matching backup: `" + filename + "`", silent=False)
        if not promptYesNo("Would you like to restore it?"):
            log("Success", "Search S3 - User chose not to restore, exiting...")
            exit(0)

        # download
        absFilename = downloadFromS3(arg("--bucket"), workdir, filename, arg("--profile"))

        # decrypt
        absFilename = decryptFile(absFilename)

        # unzip
        absFilename = gunzipFile(absFilename)
    elif  arg("fileserver"):
        print("Fileserver uploading has no been implemented yet.")

    if arg("local"):
        absFilename = searchLocal(workdir, dbSourceName, date=arg("--date"), name=arg("--name"))

        # found a backup, prompt for confirmation
        say("Found matching backup: `" + filename + "`", silent=False)
        if not promptYesNo("Would you like to restore it?"):
            log("Success", "Search S3 - User chose not to restore, exiting...")
            exit(0)
        
    # check that we actually downloaded a file
    if not absFilename:
        log("ERROR", "Database download failed for `" + dbname + "`")
        say("Error downloading database backup!")
        exit(1)

    # restore!
    if arg("--peer"):
        restoreDatabasePeer(dbName, absFilename, dbUser, dbPort)
    else:
        restoreDatabasePassword(dbName, absFilename, dbUser, dbPass, dbHost, dbPort)

elif arg("push") or arg("pull") or arg("list"):
    print("The `push`, `pull`, and `list` commands have not been implemented yet.")

# clean old backups ()
if arg("cleanup") or arg("alwaysCleanup"):
    print("The cleanup feature has no been implemented yet.")
