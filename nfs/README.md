# pve-ontap-snapshot.py

## Motivation

One of the advantages of virtualization is the ability to easily create snapshots of the virtual machines disk image. Proxmox offers this ability for the `qcow2` disk image format, but not for the `raw` format on NFS mounted storage. When creating a VM disk snapshot with Proxmox on the local filesystem, Proxmox uses the features of the underlying filesystem to create snapshots. In cases where the VM disk is placed on a NFS storage it is not possibly for Proxmox to use any filesystem features to create a snapshot and has to fallback to file based snapshots. File based snapshots have a huge negative impact on the VMs disk performance.

Since NetApp ONTAP offers different snapshot features on file level and on volume level, it would be nice to use this features with Proxmox. This script tries to make this features usable with Proxmox as a proof-of-concept.

The common way to connect Proxmox with NetApp ONTAP would be NFS, NetApp ONTAP also supports iSCSI, but that is out of scope for this script, as an iSCSI connected storage offers full access to the filesystem and therefore Proxmox can use the filesystem features.

The advantage of using NFS as storage is that multiple Proxmox nodes in a cluster can access the VM disk which makes migration of a VM between Proxmox nodes fast and easy. Both NetApp ONTAP and the Linux kernel support `nconnect` for NFS which increases the NFS performance by allowing multiple TCP connections to the NFS server. To enable this for a NFS storage in Proxmox add the `nconnect` option to the storage configuration in `/etc/pve/storage.cfg`.
```bash
$ cat /etc/pve/storage.cfg
...
nfs: ONTAP01
        export /proxmox_storage01
        path /mnt/pve/ONTAP01
        server 172.16.60.60
        content images
        prune-backups keep-all=1
        options nconnect=16
...
```
## Features

### VM disk snapshots

`pve-ontap-snapshot.py` uses the NetApp ONTAP feature ObjectClone to create a copy of a VM disk image. The ObjectClone creates the copy by linking to the same blocks on the filesystem, changes on any copy is written to new blocks. Therefore creating the ObjectClone copy is very fast and space efficient. Restoring a snapshot is renaming the ObjectClone copy to the original filename, no time and performance consuming merging of snapshot files is necessary.

Creating ObjectClone snapshots from a VM disk is independent form the VM disks format, creating snapshots from `qcow2` and `raw` disk images is possible.

### Proxmox storage snapshot

NetApp ONTAP has the ability to create so called volume snapshots. A volume contains the filesystem, the snapshot is basically freezing the underlying blocks and writing any changes to new blocks, creating a volume snapshot is therefore very fast and also space efficient, since only changes after creating the snapshot take up additional space.

This volume snapshots can be used to restore a volume to its state at the moment the snapshot was taken or with the FlexClone feature to create a new volume from the snapshot. This new volume is then added to Proxmox as a new storage. 

## Config

The config file contains the credentials for the Proxmox API and the credentials for the NetApp ONTAP management interfaces. By default the `config.ini` in the current directory is used, the `-config` option can be used to overwrite the default config location.

The `proxmox` section contains the credentials for the Proxmox API, every other section is named after the storage id in Proxmox and holds the credentials of the ONTAP management interface of the ONTAP cluster exporting the filesystem via NFS.

Example:
```ini
[proxmox]
# Proxmox PVE API
proxmox_host = 172.16.60.128
proxmox_user = root@pam
proxmox_pass = SuPeRs3cr3t
proxmox_verify = false

# Each section name represents a storage id in Proxmox, the names must match
# The storage must be an ONTAP system, host points to the ONTAP management interface
[ONTAP01]
host = 192.168.38.50
user = admin
pass = SuPeRs3cr3t
verify = false

[BACKUP01]
host = 192.168.38.50
user = admin
pass = SuPeRs3cr3t
verify = false
```

## Usage

The script works in two different contexts. One is the VM context, the other is the storage context.

### VM

The VM context is used to create VM snapshots. For this ONTAP ObjectClone is used. ONTAP ObjectClone creates a copy, by pointing to the same blocks in the filesystem, changes on the copy or the original file are written in separate blocks, therefore creating this copy of a volume is incredibly fast and does not take any extra space on the filesystem. But the most important part is, accessing the snapshot is as fast as accessing the original virtual disk.

```bash
$ pve-ontap-snapshot.py vm create -vm 100 -suspend
```

### Storage

The storage context is used to create and manage snapshots of the ONTAP volume backing the Proxmox storage. It is able to `create`, `restore`, `list`, `delete`, `mount` and `unmount` volume snapshots.

#### mount

The `mount` operation creates an ONTAP FlexClone volume and adds it as an additional storage to Proxmox. 

```bash
$ pve-ontap-snapshot.py storage mount -storage ONTAP01 -snapshot proxmox_snapshot_2024-03-13_14:42:47+0000
```

This command will create the storage `ONTAP01-CLONE` in Proxmox with the content of the snapshot `proxmox_snapshot_2024-03-13_14:42:47+0000`.

#### unmount

The `unnmount` operation removes an ONTAP FlexClone volume and its storage representation in Proxmox, it does not remove the backing snapshot.

```bash
$ pve-ontap-snapshot.py storage unmount -storage ONTAP01-CLONE
```

#### list

The `list` command lists all snapshots of a given storage.

```bash
$ pve-ontap-snapshot.py storage list -storage ONTAP01
```

#### create

The `create` command creates a new snapshot of a given storage. It is advisable to suspend to disk or shutdown the VMs on the storage, if there are any, to make sure to have a consistent state, but not needed.

```bash
$ pve-ontap-snapshot.py storage create -storage ONTAP01
```

#### delete

The `delete` command deletes a given snapshot.

```bash
$ pve-ontap-snapshot.py storage delete -storage ONTAP01 -snapshot proxmox_snapshot_2024-03-13_14:42:47+0000
```

#### restore

The `restore` command reverts a given storage to a given snapshot. 

⚠️ Data loss ahead, use `mount` command instead

```bash
$ pve-ontap-snapshot.py storage restore -storage ONTAP01 -snapshot proxmox_snapshot_2024-03-13_14:42:47+0000
```

#### show

The `show` command shows the metadata of the backing ONTAP volume of a Proxmox storage. This is mostly for debugging purposes.

```bash
$ pve-ontap-snapshot.py storage show -storage ONTAP01
```

## Autocompletion

`pve-ontap-snapshot.py` uses the `argparse` module from the Python3 standard library. The third-party module `argcomplete` offers an easy and fast way to add autocompletion to a Python3 script. To enable autocompletion for `pve-ontap-storage.py` run the following command and add it to your `.bashrc`.

```bash
$ eval "$(register-python-argcomplete pve-ontap-snapshot.py)"
```

## Known limitations

### Add VM disk snapshot to Proxmox

After creating a VM disk snapshot, the new VM disk snapshot is not shown in Proxmox. To make Proxmox recognize the new VM disk a `qm rescan --vmid <VMID>` needs to be run on the Proxmox hosts command line. After running the command, the snapshot is shown as unused disk. At the moment of writing this, this command is not accessible via the Proxmox API, therefore `pve-ontap-snapshot.py` is not running the command, it is up to the user to do so. 

*Hint: Take a look at `inotify` to automate the detection of new VM disks.*

### Removed storage not unmounted by Proxmox

Not sure if it is a feature or a bug, but Proxmox does not unmount a storage when its remove from Proxmox. This causes an error, if a storage id is reused, before the old one was unmounted manually.