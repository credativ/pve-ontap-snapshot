#!/usr/bin/env python3

from proxmoxer import ProxmoxAPI
from proxmoxer import core

from netapp_ontap import HostConnection
from netapp_ontap.resources import Volume, Snapshot, FileClone, CLI
from netapp_ontap.error import NetAppRestError

from time import gmtime, strftime
import os.path
import sys
import argparse
import logging
import configparser
from pprint import pprint

# disable warnings from the urllib module
import warnings
warnings.simplefilter("ignore")

def get_volume(vol, storage):
    logging.debug(f'parameters: {vol}, {storage}')
    with HostConnection(config[storage]['host'],
                        config[storage]['user'],
                        config[storage]['pass'],
                        verify=(True if config[storage]['verify'].lower() == 'true' else False)):
        volumes = Volume.get_collection()
        for volume in volumes:
            if volume['name'] == vol:
                volume.get()
                return volume

class VM:
    def __init__(self, id, **kwargs) -> None:
        logging.debug(f'VM init parameters: {id}, {kwargs}')
        self.id = id
        # self.prox = ProxmoxAPI(config['DEFAULT']['proxmox_host'], user=config['DEFAULT']['proxmox_user'], password=config['DEFAULT']['proxmox_pass'], verify_ssl=False)
        self.prox = ProxmoxAPI(kwargs['proxmox_host'], user=kwargs['proxmox_user'], password=kwargs['proxmox_pass'], verify_ssl=(True if kwargs['proxmox_verify'].lower() == 'true' else False))
        nodes = self.prox.nodes.get()
        logging.debug(f'Found Proxmox nodes: {nodes}')
        for node in nodes:
            try:
                self.status = self.prox.nodes(node['node']).qemu(self.id).status.current.get()['status']
                self.name = self.prox.nodes(node['node']).qemu(self.id).status.current.get()['name']
                self.config = self.prox.nodes(node['node']).qemu(self.id).config.get()
                self.node = node['node']
                break
            except core.ResourceException as e:
                pass
        self.storage = {}
        for key, value in self.config.items():
            if ('ide' in key or 'sata' in key or 'scsi' in key) and ('qcow2' in value or 'raw' in value or 'vmdk' in value) and 'cdrom' not in value:
                storage_name = value.split(':')[0]
                storage_disk = value.split(':')[1].split(',')[0]
                if storage_name in self.storage:
                    self.storage[storage_name]['disks'].append(storage_disk)
                else:
                    self.storage[storage_name] = {}
                    self.storage[storage_name]['disks'] = [storage_disk]

        for storage_name in self.storage:
            self.storage[storage_name]['volume'] = self.prox.storage(storage_name).get()['export'].strip('/')

    def __str__(self) -> str:
        return f"""
            VM name:        {self.name},
            VM id:          {self.id},
            PVE connection: {self.prox},
            VM status:      {self.status},
            VM config:      {self.config},
            Storage:        {self.storage}
        """

    def add_ontap_access(self, **kwargs):
        self.ontap_host = kwargs['host']
        self.ontap_user = kwargs['user']
        self.ontap_pass = kwargs['pass']
        self.ontap_verify = True if kwargs['verify'].lower() == 'true' else False

    def shutdown(self):
        self.prox.nodes(self.node).qemu(self.name).status.shutdown.post()

    def suspend(self):
        self.prox.nodes(self.node).qemu(self.name).status.suspend.post(todisk=True)

    def start(self):
        self.prox.nodes(self.node).qemu(self.name).status.start.post()

    def create(self):
        timestamp = strftime("%Y-%m-%d_%H:%M:%S+0000", gmtime())
        for storage_name, storage_info in self.storage.items():
            volume = get_volume(storage_info['volume'], storage_name)
            with HostConnection(config[storage_name]['host'],
                                config[storage_name]['user'],
                                config[storage_name]['pass'],
                                verify=False):
                for file in storage_info['disks']:
                    vm_dir, filename = os.path.split(file)
                    snapshot_name = f'{os.path.splitext(filename)[0]}-snapshot-{timestamp}{os.path.splitext(filename)[1]}'
                    request_body = {'volume': 
                                        {'name': volume.name,
                                         'uuid': volume.uuid},
                                    'source_path': f'images/{file}',
                                    'destination_path': f'images/{vm_dir}/{snapshot_name}',
                                    'overwrite_destination': False
                                    }
                    file_clone = FileClone(**request_body)
                    file_clone.post()

class Storage:
    def __init__(self, storage) -> None:
        self.storage = storage
        self.prox = ProxmoxAPI(config['DEFAULT']['proxmox_host'], user=config['DEFAULT']['proxmox_user'], password=config['DEFAULT']['proxmox_pass'], verify_ssl=False)
        self.volume_name = self.prox.storage(storage).get()['export'].strip('/')

    def __str__(self) -> str:
        return f"""
            Storage name:   {self.storage},
            Storage volume: {self.volume_name}
        """

    def create(self):
        volume = get_volume(self.volume_name, self.storage)
        timestamp = strftime("%Y-%m-%d_%H:%M:%S+0000", gmtime())
        snapshot = Snapshot.from_dict({
            "name": f'proxmox_snapshot_{timestamp}',
            "comment": f"Snapshot of Proxmox storage {self.storage}",
            "volume": volume.to_dict()
        })
        logging.debug(snapshot)
        with HostConnection(config[self.storage]['host'],
                            config[self.storage]['user'],
                            config[self.storage]['pass'],
                            verify=False):
            snapshot.post()

    def restore(self, snapshot):
        volume = get_volume(self.volume_name, self.storage)
        with HostConnection(config[self.storage]['host'],
                            config[self.storage]['user'],
                            config[self.storage]['pass'],
                            verify=False):
            CLI().execute('volume snapshot restore', vserver=volume.svm.name, volume=volume.name, snapshot=snapshot, force=True)

    def delete(self, snapshot):
        volume = get_volume(self.volume_name, self.storage)
        with HostConnection(config[self.storage]['host'],
                            config[self.storage]['user'],
                            config[self.storage]['pass'],
                            verify=False):
            avail_snaps = Snapshot.get_collection(volume.uuid)
            for snap in avail_snaps:
                if snapshot == snap.name:
                    snap.delete()

    def list(self):
        volume = get_volume(self.volume_name, self.storage)
        with HostConnection(config[self.storage]['host'],
                            config[self.storage]['user'],
                            config[self.storage]['pass'],
                            verify=False):
            avail_snaps = Snapshot.get_collection(volume.uuid)
            for snap in avail_snaps:
                if 'proxmox_snapshot_' in snap.name:
                    snap.get()
                    print(f'Name: {snap.name}, Comment: {snap.comment}')

    def mount(self, snapshot):
        parent_volume = get_volume(self.volume_name, self.storage)
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
        with HostConnection(config[self.storage]['host'],
                            config[self.storage]['user'],
                            config[self.storage]['pass'],
                            verify=False):
            try:
                volume.post(hydrate=True)
            except NetAppRestError as e:
                print(e)

        store = self.prox.storage(self.storage).get()
        result = self.prox.storage.post(storage=f'{self.storage}-CLONE', server=store['server'], type=store['type'], content=store['content'], export=f'/{self.volume_name}_clone')
        
    def unmount(self):
        volume = get_volume(self.volume_name, self.storage.removesuffix('-CLONE'))
        if not volume.clone.is_flexclone:
            print(f'{self.storage} is not a mounted volume snapshot!')
            sys.exit(1)

        self.prox.storage(self.storage).delete()
        with HostConnection(config[self.storage.removesuffix('-CLONE')]['host'],
                            config[self.storage.removesuffix('-CLONE')]['user'],
                            config[self.storage.removesuffix('-CLONE')]['pass'],
                            verify=False):
            volume.delete(force=True)

    def show(self):
        volume = get_volume(self.volume_name, self.storage.removesuffix('-CLONE'))
        pprint(volume.to_dict())


def vm_create(args):
    '''
        creates a snapshot from a running vm, optional suspended or shutdown
        uses the ONTAP volume snapshot function
        store vm info in snapshot name
    '''
    vm = VM(args.vm, **config['DEFAULT'])
    logging.debug(str(vm))

    start = False
    if args.shutdown:
        vm.shutdown()
        start = True

    if args.suspend:
        vm.suspend()
        start = True

    if vm.status != 'stopped':
        logging.warning('creating snapshot of a running vm, the result might be inconsistent')

    vm.create()

    if start:
        vm.start()

def storage_create(args):
    storage = Storage(args.storage)
    storage.create()

def storage_restore(args):
    storage = Storage(args.storage)
    storage.restore(args.snapshot)

def storage_delete(args):
    storage = Storage(args.storage)
    storage.delete(args.snapshot)

def storage_list(args):
    storage = Storage(args.storage)
    storage.list()

def storage_mount(args):
    storage = Storage(args.storage)
    storage.mount(args.snapshot)

def storage_unmount(args):
    storage = Storage(args.storage)
    storage.unmount()

def storage_show(args):
    storage = Storage(args.storage)
    storage.show()

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser_main = parser.add_argument('-config', type=str, default='config.ini', help='Path to config file, default is "./config.ini"')
    parser_main = parser.add_argument('-loglevel', choices=['info', 'warn', 'error', 'debug'], default='info', help='Set the loglevel, default is info')
    subparsers = parser.add_subparsers(title='Proxmox operations', required=True)
    subparser_vm = subparsers.add_parser('vm', help='Snapshots in VM context')
    subparser_storage = subparsers.add_parser('storage', help='Snapshots in Storage context')

    parser_vm = subparser_vm.add_subparsers(title='VM actions', required=True)
    parser_vm_create = parser_vm.add_parser('create', help='Create a VM snapshot using ONTAP ObjectClone')
    parser_vm_create.add_argument('-vm', type=int, required=True, help='Proxmox VM ID')
    parser_vm_create.add_argument('-suspend', type=bool, default=False, help='Suspend VM before creating the snapshot')
    parser_vm_create.add_argument('-shutdown', type=bool, default=False, help='Shutdown the VM before creating the snapshot')
    parser_vm_create.set_defaults(func=vm_create)

    parser_storage = subparser_storage.add_subparsers(title='Storage actions', required=True)
    parser_storage_create = parser_storage.add_parser('create', help='Create a snapshot of the storage using ONTAP volume snapshot')
    parser_storage_create.add_argument('-storage', type=str, required=True, help='Proxmox Storage ID')
    parser_storage_create.set_defaults(func=storage_create)
    parser_storage_restore = parser_storage.add_parser('restore', help='Restore an ONTAP volume snapshot to the storage')
    parser_storage_restore.add_argument('-storage', type=str, required=True, help='Proxmox Storage ID')
    parser_storage_restore.add_argument('-snapshot', type=str, required=True, help='Snapshot to restore')
    parser_storage_restore.set_defaults(func=storage_restore)
    parser_storage_delete = parser_storage.add_parser('delete', help='Delete an ONTAP volume snapshot')
    parser_storage_delete.add_argument('-storage', type=str, required=True, help='Proxmox Storage ID')
    parser_storage_delete.add_argument('-snapshot', type=str, required=True, help='Snapshot to delete')
    parser_storage_delete.set_defaults(func=storage_delete)
    parser_storage_list = parser_storage.add_parser('list', help='List all ONTAP volume snapshots')
    parser_storage_list.add_argument('-storage', type=str, required=True, help='Proxmox Storage ID')
    parser_storage_list.set_defaults(func=storage_list)
    parser_storage_mount = parser_storage.add_parser('mount', help='Mount an ONTAP volume snapshot and add it as new storage to PVE')
    parser_storage_mount.add_argument('-storage', type=str, required=True, help='Proxmox Storage ID')
    parser_storage_mount.add_argument('-snapshot', type=str, required=True, help='Snapshot to mount')
    parser_storage_mount.set_defaults(func=storage_mount)
    parser_storage_unmount = parser_storage.add_parser('unmount', help='Unmount an ONTAP volume snapshot and remove its storage from PVE')
    parser_storage_unmount.add_argument('-storage', type=str, required=True, help='Proxmox Storage ID')
    parser_storage_unmount.set_defaults(func=storage_unmount)
    parser_storage_show = parser_storage.add_parser('show', help='Show metadata of the underlying ONTAP volume')
    parser_storage_show.add_argument('-storage', type=str, required=True, help='Proxmox Storage ID')
    parser_storage_show.set_defaults(func=storage_show)

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
