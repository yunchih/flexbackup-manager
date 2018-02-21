
# Flexbackup Scheduling Script

## Introduction
This script is a backup scheduler built upon flexbackup
that manages backup set according to their tiers and
corresponding SLAs.  We currently support two tiers with
different SLAs.

The tier with higher SLA will have the following properties:

1. More frequent full backup.
2. More dated full/incremental backup stored.
3. More frequent incremental backup.

The incentives of such distinction are as followed:

1. When there's data loss, the dataset can be recovered faster.
2. Make it possible for user to recover very old snapshot of their data.
3. The data is being actively used, thus daily incremental
backup can effectively catch daily delta.  On the other hand,
Tier 2 data is rarely updated, thus frequent incremental backup
can be a waste.

## Functionality

#### Configuration file

Backup set is read from a yaml file, whose format is expected to be:

``` yaml
   root_directory: "/e"            # The directory where original data is stored
   dest_directory: "/backup/nfs"   # The directory where backup data is stored
   exclude_patterns:
     - PATTERN_A
     - PATTERN_B
   subdirectory_expansions:        # Whether or not to backup subdirectory separatedly
       A: true
       B: true
       C: false
   incremental_backup_frequency:
       tier1: 1 # daily
       tier2: 3 # once every 3 day
   backup_tiers:                   # The names of data
       tier1:
           - - A
             - B
       tier2:
           - - C
```

In the above example, /e/A, /e/B will be backuped in the same day in Tier 1,
/e/C will be backuped individually in Tier 2.

#### Incremental Backup:

Tier N has daily incremental backup, while Tier 2 is once every M day, where
N < M.

#### Full backup

Full backup is scheduled statically according to "backup cycle".
A backup cycle consists of two tier1 and one tier2.
tier1 runs first, then followed by interception of
tier2 and the other tier1.  Here's an example full
cycle:

```
a: tier1
b: tier2
a1, a2, a3, b1, a1, b2, a2, b3, a3, a1, a2, a3, b1, ....
|---------------------------------|
            one cycle
```

Their mean-time-between-backup are:

```
   tier1: len(tier1) + len(tier2)/2
   tier2: len(tier1)*2 + len(tier2)
```

A longer mean-time-between-backup means longer
recovery time.

#### Backup retention period

Tier 1 keeps two set of full/incremental backup data, while Tier 2 keeps one.
See the CONF_BACKUP_TIER*_RETENTION option in the source file
