#!/usr/bin/env python3
#
# Copyright 2024 NetApp
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# PYTHON_ARGCOMPLETE_OK

"""Create ONTAP snapshots for Proxmox

Classes()
--------

VM()
    Handel VM snapshots

    Methods()
    ---------

        shutdown()
            Shutdown the VM represented by the VM object

        suspend()
            Suspend the VM represented by the VM object

        start()
            Start the VM represented by the VM object

        create()
            Create snapshots of the VMs disks using ObjectClone

Storage()
    Handel storage snapshots

    Methods()
    ---------

    add_vm_disk()
        Add a VM disk to the Storage object

    create()
        Create a snapshot of the Storage object using volume snapshots

    restore()
        Restore a volume from a given volume snapshot

    delete()
        Delete a volume snapshot

    list()
        List a volume snapshots of a Storage object

    mount()
        Mount a snapshot using FlexClone and add is a storage to Proxmox

    unmount()
        Remove a FlexClone volume and remove its storage from Proxmox

    show()
        Show metadata of the volume backing the storage

Functions()
-----------

get_volume()

    Return volume object from volume name

caller()
    Create object and call one of its methods as requested by the cmdline args

"""

from proxmoxer import ProxmoxAPI
from proxmoxer import ResourceException

from netapp_ontap import HostConnection
from netapp_ontap.resources import Volume, Snapshot, FileClone, CLI
from netapp_ontap.error import NetAppRestError

from time import gmtime, strftime, sleep
import os.path
import sys
import argparse, argcomplete
import logging
import configparser
from pprint import pprint

# disable warnings from the urllib module
import warnings
warnings.simplefilter("ignore")

def get_volume(vol, access):
    """Return volume object from volume name"""
    logging.debug(f'parameters: {vol}, {access}')
    with HostConnection(access['host'],
                        access['user'],
                        access['pass'],
                        verify=access['verify']):
        volumes = Volume.get_collection()
        for volume in volumes:
            if volume['name'] == vol:
                volume.get()
                return volume

class VM:
    def __init__(self, id, config) -> None:
        """Initialise VM object"""
        logging.debug(f'VM init parameters: {id}, {dict(config)}')
        self.id = id
        self.prox = ProxmoxAPI(config['proxmox']['proxmox_host'], user=config['proxmox']['proxmox_user'], password=config['proxmox']['proxmox_pass'], verify_ssl=(True if config['proxmox']['proxmox_verify'].lower() == 'true' else False))
        nodes = self.prox.nodes.get()
        logging.debug(f'Found Proxmox nodes: {nodes}')
        for node in nodes:
            try:
                self.status = self.prox.nodes(node['node']).qemu(self.id).status.current.get()['status']
                self.name = self.prox.nodes(node['node']).qemu(self.id).status.current.get()['name']
                self.config = self.prox.nodes(node['node']).qemu(self.id).config.get()
                self.node = node['node']
                break
            except ResourceException as e:
                pass
        self.storages = []
        for key, value in self.config.items():
            if ('ide' in key or 'sata' in key or 'scsi' in key) and ('qcow2' in value or 'raw' in value or 'vmdk' in value) and 'cdrom' not in value:
                storage_name = value.split(':')[0]
                storage_disk = value.split(':')[1].split(',')[0]
                storage = Storage(storage_name, config)
                storage.add_vm_disk(storage_disk)
                self.storages.append(storage)

    def __str__(self) -> str:
        """Print VM object in human readable format"""
        return f"""
            VM name:        {self.name},
            VM id:          {self.id},
            PVE connection: {self.prox},
            VM status:      {self.status},
            VM config:      {self.config},
            Storage:        {([str(storage).strip() for storage in self.storages])}
        """

    def shutdown(self):
        """Shutdown the VM represented by the VM object"""
        logging.info(f'shutting down vm {self.id} ({self.name})...')
        try:
            task = self.prox.nodes(self.node).qemu(self.id).status.shutdown.post()
            logging.debug(f'upid: {task}')
            while True:
                status = self.prox.nodes(self.node).tasks(task).status.get()['status']
                logging.debug(status)
                if status == 'stopped':
                    self.status = self.prox.nodes(self.node).qemu(self.id).status.current.get()['status']
                    break
                else:
                    sleep(1)
        except ResourceException as e:
            logging.error(e)
            sys.exit(1)
        logging.info(f'...done')

    def suspend(self):
        """Suspend the VM represented by the VM object"""
        logging.info(f'suspending vm {self.id} ({self.name})...')
        try:
            task = self.prox.nodes(self.node).qemu(self.id).status.suspend.post(todisk=1)
            logging.debug(f'upid: {task}')
            while True:
                status = self.prox.nodes(self.node).tasks(task).status.get()['status']
                logging.debug(status)
                if status == 'stopped':
                    self.status = self.prox.nodes(self.node).qemu(self.id).status.current.get()['status']
                    break
                else:
                    sleep(1)
        except ResourceException as e:
            logging.error(e)
            sys.exit(1)
        logging.info(f'...done')

    def start(self):
        """Start the VM represented by the VM object"""
        logging.info(f'starting vm {self.id} ({self.name})...')
        try:
            task = self.prox.nodes(self.node).qemu(self.id).status.start.post()
            logging.debug(f'upid: {task}')
            while True:
                status = self.prox.nodes(self.node).tasks(task).status.get()['status']
                logging.debug(status)
                if status == 'running':
                    break
                else:
                    sleep(1)
        except ResourceException as e:
            logging.error(e)
            sys.exit(1)
        logging.info(f'...done')

    def create(self, suspend=False, shutdown=False):
        """Create snapshots of the VMs disks using ObjectClone"""
        if suspend:
            self.suspend()
        if shutdown:
            self.shutdown()
        if self.status != 'stopped':
            logging.warning('creating snapshot of a running vm, the result might be inconsistent')

        logging.info(f'creating vm {self.id} ({self.name}) disk snapshot...')
        timestamp = strftime("%Y-%m-%d_%H:%M:%S+0000", gmtime())
        for storage in self.storages:
            volume = get_volume(storage.volume_name, storage.access)
            vm_dir, filename = os.path.split(storage.disk)
            snapshot_name = f'{os.path.splitext(filename)[0]}-snapshot-{timestamp}{os.path.splitext(filename)[1]}'
            request_body = {'volume':
                                {'name': volume.name,
                                    'uuid': volume.uuid},
                            'source_path': f'images/{storage.disk}',
                            'destination_path': f'images/{vm_dir}/{snapshot_name}',
                            'overwrite_destination': False
                            }
            file_clone = FileClone(**request_body)
            with HostConnection(storage.access['host'],
                                storage.access['user'],
                                storage.access['pass'],
                                verify=storage.access['verify']):
                file_clone.post()
        logging.info(f'...done')
        if suspend or shutdown:
            self.start()

class Storage:
    def __init__(self, storage, config) -> None:
        """Initialise Storage object"""
        logging.debug(f'Storage init parameters: {storage}, {dict(config)}')
        self.storage = storage
        self.prox = ProxmoxAPI(config['proxmox']['proxmox_host'], user=config['proxmox']['proxmox_user'], password=config['proxmox']['proxmox_pass'], verify_ssl=(True if config['proxmox']['proxmox_verify'].lower() == 'true' else False))
        try:
            self.volume_name = self.prox.storage(storage).get()['export'].strip('/')
        except ResourceException as e:
            logging.error(e)
            sys.exit(1)
        self.access = dict(config[storage.removesuffix('-CLONE')])
        self.access['verify'] = True if self.access['verify'].lower() == 'true' else False
        self.disk = ''

    def __str__(self) -> str:
        """Print Storage object in human readable format"""
        return f"""
            Storage name:   {self.storage},
            PVE connection: {self.prox},
            Storage volume: {self.volume_name},
            Storage access: {self.access},
            VM disk:        {self.disk}
        """

    def add_vm_disk(self, disk_name):
        """Add a VM disk to the Storage object"""
        self.disk = disk_name

    def create(self):
        """Create a snapshot of the Storage object using volume snapshots"""
        logging.info(f'creating storage {self.storage} snapshot...')
        volume = get_volume(self.volume_name, self.access)
        timestamp = strftime("%Y-%m-%d_%H:%M:%S+0000", gmtime())
        snapshot = Snapshot.from_dict({
            "name": f'proxmox_snapshot_{timestamp}',
            "comment": f"Snapshot of Proxmox storage {self.storage}",
            "volume": {'name': volume.name, 'uuid': volume.uuid}
        })
        logging.debug(snapshot)
        with HostConnection(self.access['host'],
                            self.access['user'],
                            self.access['pass'],
                            verify=self.access['verify']):
            snapshot.post()
        logging.info(f'...done')

    def restore(self, snapshot):
        """Restore a volume from a given volume snapshot"""
        logging.info(f'restore snapshot {snapshot} to storage {self.storage}...')
        volume = get_volume(self.volume_name, self.access)
        with HostConnection(self.access['host'],
                            self.access['user'],
                            self.access['pass'],
                            verify=self.access['verify']):
            CLI().execute('volume snapshot restore', vserver=volume.svm.name, volume=volume.name, snapshot=snapshot, force=True)
        logging.info(f'...done')

    def delete(self, snapshot):
        """Delete a volume snapshot"""
        logging.info(f'deleting snapshot {snapshot}...')
        volume = get_volume(self.volume_name, self.access)
        with HostConnection(self.access['host'],
                            self.access['user'],
                            self.access['pass'],
                            verify=self.access['verify']):
            avail_snaps = Snapshot.get_collection(volume.uuid)
            for snap in avail_snaps:
                if snapshot == snap.name:
                    snap.delete()
        logging.info(f'...done')

    def list(self):
        """List a volume snapshots of a Storage object"""
        logging.info(f'list storage {self.storage} snapshots...')
        volume = get_volume(self.volume_name, self.access)
        with HostConnection(self.access['host'],
                            self.access['user'],
                            self.access['pass'],
                            verify=self.access['verify']):
            available_snapshots = Snapshot.get_collection(volume.uuid)
            for snapshot in available_snapshots:
                if 'proxmox_snapshot_' in snapshot.name:
                    snapshot.get()
                    logging.info(f'Name: {snapshot.name}, Comment: {snapshot.comment}')
        logging.info(f'...done')

    def mount(self, snapshot):
        """Mount a snapshot using FlexClone and add is a storage to Proxmox"""
        logging.info(f'mounting volume {self.storage} snapshot...')
        parent_volume = get_volume(self.volume_name, self.access)
        request_body = {'name': f'{self.volume_name}_clone',
                        'svm': {'name': parent_volume.svm.name},
                        'clone': {
                            'parent_volume': {'name': self.volume_name},
                            'parent_snapshot': {'name': snapshot},
                            'is_flexclone': True,
                            'type': 'rw'
                                 },
                        'nas': {'path': f'/{self.volume_name}_clone'}
                       }
        volume = Volume(**request_body)
        with HostConnection(self.access['host'],
                            self.access['user'],
                            self.access['pass'],
                            verify=self.access['verify']):
            try:
                volume.post(hydrate=True)
            except NetAppRestError as e:
                logging.error(e)

        store = self.prox.storage(self.storage).get()
        self.prox.storage.post(storage=f'{self.storage}-CLONE', server=store['server'], type=store['type'], content=store['content'], export=f'/{self.volume_name}_clone')
        logging.info(f'...done')
        
    def unmount(self):
        """Remove a FlexClone volume and remove its storage from Proxmox"""
        logging.info(f'unmounting mounted volume snapshot {self.storage}...')
        volume = get_volume(self.volume_name, self.access)
        if not volume.clone.is_flexclone:
            logging.error(f'{self.storage} is not a mounted volume snapshot!')
            sys.exit(1)

        self.prox.storage(self.storage).delete()
        with HostConnection(self.access['host'],
                            self.access['user'],
                            self.access['pass'],
                            verify=self.access['verify']):
            volume.delete(force=True)
        logging.info(f'...done')

    def show(self):
        """Show metadata of the volume backing the storage"""
        volume = get_volume(self.volume_name, self.access)
        pprint(volume.to_dict())

def caller(args):
    """Create object and call one of its methods as requested by the cmdline args"""
    logging.debug(args)
    context = args.context((args.vm if 'vm' in args else args.storage), config)
    logging.debug(str(context))
    cmd = getattr(context, args.cmd)
    parameters = {}
    for arg, value in vars(args).items():
        if arg not in ['config', 'loglevel', 'vm', 'storage', 'func', 'context', 'cmd']:
            parameters[arg] = value
    logging.debug(f'options: {parameters}')
    cmd(**parameters)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-config', type=str, default='config.ini', help='Path to config file, default is "./config.ini"')
    parser.add_argument('-loglevel', choices=['info', 'warn', 'error', 'debug'], default='info', help='Set the loglevel, default is info')
    parser.set_defaults(func=caller)
    subparsers = parser.add_subparsers(title='Proxmox operations', required=True)
    subparser_vm = subparsers.add_parser('vm', help='Snapshots in VM context')
    subparser_storage = subparsers.add_parser('storage', help='Snapshots in Storage context')

    parser_vm = subparser_vm.add_subparsers(title='VM actions', required=True)
    parser_vm_create = parser_vm.add_parser('create', help='Create a VM snapshot using ONTAP ObjectClone')
    parser_vm_create.add_argument('-vm', type=int, required=True, help='Proxmox VM ID')
    parser_vm_create.add_argument('-suspend', action='store_true', help='Suspend VM before creating the snapshot')
    parser_vm_create.add_argument('-shutdown', action='store_true', help='Shutdown the VM before creating the snapshot')
    parser_vm_create.set_defaults(context=VM, cmd='create')

    parser_storage = subparser_storage.add_subparsers(title='Storage actions', required=True)
    parser_storage_create = parser_storage.add_parser('create', help='Create a snapshot of the storage using ONTAP volume snapshot')
    parser_storage_create.add_argument('-storage', type=str, required=True, help='Proxmox Storage ID')
    parser_storage_create.set_defaults(context=Storage, cmd='create')

    parser_storage_restore = parser_storage.add_parser('restore', help='Restore an ONTAP volume snapshot to the storage')
    parser_storage_restore.add_argument('-storage', type=str, required=True, help='Proxmox Storage ID')
    parser_storage_restore.add_argument('-snapshot', type=str, required=True, help='Snapshot to restore')
    parser_storage_restore.set_defaults(context=Storage, cmd='restore')

    parser_storage_delete = parser_storage.add_parser('delete', help='Delete an ONTAP volume snapshot')
    parser_storage_delete.add_argument('-storage', type=str, required=True, help='Proxmox Storage ID')
    parser_storage_delete.add_argument('-snapshot', type=str, required=True, help='Snapshot to delete')
    parser_storage_delete.set_defaults(context=Storage, cmd='delete')

    parser_storage_list = parser_storage.add_parser('list', help='List all ONTAP volume snapshots')
    parser_storage_list.add_argument('-storage', type=str, required=True, help='Proxmox Storage ID')
    parser_storage_list.set_defaults(context=Storage, cmd='list')

    parser_storage_mount = parser_storage.add_parser('mount', help='Mount an ONTAP volume snapshot and add it as new storage to PVE')
    parser_storage_mount.add_argument('-storage', type=str, required=True, help='Proxmox Storage ID')
    parser_storage_mount.add_argument('-snapshot', type=str, required=True, help='Snapshot to mount')
    parser_storage_mount.set_defaults(context=Storage, cmd='mount')

    parser_storage_unmount = parser_storage.add_parser('unmount', help='Unmount an ONTAP volume snapshot and remove its storage from PVE')
    parser_storage_unmount.add_argument('-storage', type=str, required=True, help='Proxmox Storage ID')
    parser_storage_unmount.set_defaults(context=Storage, cmd='unmount')

    parser_storage_show = parser_storage.add_parser('show', help='Show metadata of the underlying ONTAP volume')
    parser_storage_show.add_argument('-storage', type=str, required=True, help='Proxmox Storage ID')
    parser_storage_show.set_defaults(context=Storage, cmd='show')

    argcomplete.autocomplete(parser)
    args = parser.parse_args()

    config = configparser.ConfigParser()
    config.read(args.config)

    logLevel = {
        'info': logging.INFO,
        'warn': logging.WARN,
        'error': logging.ERROR,
        'debug': logging.DEBUG
    }

    logFormat = "%(levelname)s:%(filename)s:%(funcName)s:%(message)s"

    logging.basicConfig(format=logFormat, level=logLevel[args.loglevel])
    args.func(args)
