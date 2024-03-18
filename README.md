# pve-ontap-snapshot.py

## Motivation

NetApp ONTAP offers different snapshot options on file level and on volume level. To make this options easy to use with Proxmox, this script offers direct access to the options and tries to make the result accessible in Proxmox.

## Performance
TBD

## Config

The config file contains the credentials for the Proxmox API and the credentials for the NetApp ONTAP management interfaces. By default the `config.ini` in the current directory is used, the `-config` option can the used to overwrite the default config location.

The `DEFAULT` section contains the credentials for the Proxmox API, every other sections is named after the storage id in Proxmox and holds the credentials of the ONTAP management interface of the ONTAP cluster exporting the filesystem via NFS.

Example:
```ini
[DEFAULT]
# Proxmox PVE API
proxmox_host = 172.16.60.128
proxmox_user = root@pam
proxmox_pass = SuPeRs3cr3t

# Each section name represents a storage id in Proxmox, the names must match
# The storage must be an ONTAP system, host points to the ONTAP management interface
[ONTAP01]
host = 192.168.38.50
user = admin
pass = SuPeRs3cr3t

[BACKUP01]
host = 192.168.38.50
user = admin
pass = SuPeRs3cr3t
```

## Usage

The script works in two different contexts. One is the VM context, the other is the storage context.

### VM

The VM context is used to create VM snapshots. For this ONTAP ObjectClone ist used. ONATP ObjectClone creates a copy, but pointing to the same blocks in the filesystem, changes on the copy or the original file written in separate blocks, therefore creating this copy of a volume is incredibly fast and does not take any extra space on the filesystem. But the most important part is, accessing the snapshot is as fast as accessing the original virtual disk.

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

The `unnmount` operation removes an ONTAP FlexClone volume and its storage representation in Proxmmox, it does not remove the backing snapshot.

```bash
$ pve-ontap-snapshot.py storage unmount -storage ONTAP01-CLONE
```

#### list

The `list` command lists all snapshots of a given storage.

```bash
$ pve-ontap-snapshot.py storage list -storage ONTAP01
```

#### create

The `create` command create a new snapshot of a given storage. It is advisable to suspend to disk or shutdown the VMs on the storage, if there are any, to make sure to have a consistent state, but not needed.

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

`pve-ontap-snapshot.py` uses the `argparse` module from the Python3 standard library. The third-party module `argcomplete` offers an easy and fast way to add autocompletion to a Python3 script. To enable autocompletion for `pve-ontap-storage.py` run following command and add it to your `.bashrc`.

```bash
$ eval "$(register-python-argcomplete pve-ontap-snapshot.py)"
```

## Problems

### Add VM disk snapshot to Proxmox

After creating a VM disk snapshot, the new VM disk snapshot is not shown in Proxmox. To make Proxmox recognize the new VM disk a `qm rescan --vmid <VMID>` needs to be run on the Proxmox hosts command line. After running the command, the snapshot is shown as unused disk. At the moment of writing this, this command is not accessible via the Proxmox API, therefore `pve-ontap-snapshot.py` is not running the command, it is up to the user to do so. 

Tip: Take a look at `inotify` to automate the detetion of new VM disks.

### Removed storage not unmounted by Proxmox

Not sure if it is a feature or a bug, but Proxmox does not unmount a storage when its remove from Proxmox. This causes an error, if a storage id is reused, before the old one was unmounted manually.