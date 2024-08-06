import fnmatch
import logging

from .base import Plugin
from ..common import *


class FileTransferPlugin(Plugin):
    """Receive file from phone"""
    MARK = b'file'
    NAME = 'FileTransferPlugin'
    MAIN_CONF = dict()
    DEVICE_CONFS = dict()
    CONFIG_SCHEMA = DictEntry('file.conf.json', 'Common configuration for file transfer plugin', False, entries=(
        IntEntry('uin', 'UIN of device for which config will be applied', True, 1, 0xFFFFFFF, None),
        DirEntry('download_directory', 'Directory to save downloaded files', False, '/tmp/dconnect', True, False),
        ListEntry('shared_dirs', 'Directories shared to client', False, 0, 1073741824, (),
                  entry=DictEntry('shared_dirs[]', 'Description of shared directory', False, entries=(
                      DirEntry('path', 'Path to shared directory', False, '/tmp/dconnect', True, False),
                      StringEntry('name', 'Name using for directory instead of path', True, 0, 60, 'Shared'),
                      StringEntry('glob', 'UNIX glob to filter visible files in directory', False, 0, 1073741824, '*'),
                      IntEntry('deep', 'Recursion deep for subdirectories', False, 1, 1024, 1)
                  )))
    ))
    PART = 65532
    shared_files_index = list()

    def check_file_filter(self, path: str, glob_str: str) -> bool:
        """Check if file allowed for sharing by filter"""
        return fnmatch.fnmatch(path, glob_str)

    def shared_directory_list(self, directory: str, filter_data, max_deep, current_deep):
        """Create information node for one shared directory"""
        res = list()
        try:
            dir_list = os.listdir(directory)
        except (PermissionError, OSError) as e:
            self.log(f'Could not list content of directory "{directory}" ({e})')
            return res
        for name in dir_list:
            path = os.path.join(directory, name)
            if os.path.isdir(path):
                if current_deep < max_deep and max_deep > 0:
                    dir_list = self.shared_directory_list(path, filter_data, max_deep, current_deep + 1)
                    res.append(dict(name=name, node_type='directory', size=len(dir_list), children=dir_list))
            elif os.path.isfile(path):
                if self.check_file_filter(path, filter_data):
                    self.shared_files_index.append(path)
                    index=len(self.shared_files_index) - 1
                    res.append(dict(name=name, node_type='file', size=os.path.getsize(path), index=index))
        return res

    def shared_files_info(self) -> list:
        """Create tree structure of shared directories"""
        self.shared_files_index.clear()
        res = list()
        names = dict()
        for shared_dir in self.conf('shared_dirs'):
            path, name = shared_dir['path'], shared_dir['name']
            if not os.path.isdir(path):
                self.log('Shared directory "{}" not found'.format(path), logging.WARN)
                continue
            if name is None:
                name = os.path.basename(path)
            if name in names:
                names[name] += 1
                name += ' ({})'.format(names[name])
            else:
                names[name] = 0
            dir_list = self.shared_directory_list(path, shared_dir['glob'], shared_dir.get('deep', 0), 1)
            res.append(dict(name=name, node_type='directory', size=len(dir_list), children=dir_list))
        return res

    def handle_upload(self, request):
        """Receive and save file from client"""
        try:
            name, size = request.params['name'], request.params['size']
        except KeyError as e:
            self.log('KeyError {}'.format(e), logging.WARN)
        else:
            path = os.path.join(self.conf('download_directory'), name)
            self.log('Receiving {} bytes to file {}'.format(size, path))
            self.rpc_send(RPCResponse(request.id, dict(code=0, message='OK')))
            f = open(path, 'wb')
            wrote = 0
            while wrote < size:
                buf = self.read()
                if buf is None:
                    self.log('File receiving aborted ({} bytes received)'.format(wrote), logging.WARN)
                    return
                if len(buf) == 0:
                    req = self.rpc_read()
                    if req.method == "cancel":
                        self.log('File receiving canceled by client ({} bytes received)'.format(wrote), logging.INFO)
                        f.close()
                        self.rpc_send(RPCResponse(request.id, dict(code=1, message='Canceled')))
                        return
                wrote += len(buf)
                f.write(buf)
            f.close()
            self.log('File received ({} bytes)'.format(wrote), logging.INFO)
            self.rpc_send(RPCResponse(request.id, dict(code=0, message='OK')))

    def handle_list_shared(self, request):
        """Create shared files info and return as JSON"""
        try:
            result = self.shared_files_info()
        except Exception as e:
            self.logger.exception('[FileTransferPlugin] {}'.format(e))
            result = INTERNAL_ERROR
        self.rpc_send(RPCResponse(request.id, result))

    def handle_download(self, request):
        """Handle try of device to download file from server"""
        try:
            index, size = request.params['index'], request.params['size']
        except KeyError as e:
            self.log('KeyError {}'.format(e), logging.WARN)
        else:
            self.log('Download request is correct')
            if 0 <= index < len(self.shared_files_index):
                path = self.shared_files_index[index]
                if not os.path.isfile(path):
                    self.rpc_send(RPCResponse(request.id, dict(code=2, message='No such file')))
                    return
                file_size = os.path.getsize(path)
                if file_size != size:
                    self.rpc_send(RPCResponse(request.id, dict(code=2, message='Size mismatch')))
                    return
                self.rpc_send(RPCResponse(request.id, dict(code=0, message='OK')))
                with open(path, 'rb') as f:
                    self.log('Start file transmission')
                    while True:
                        chunk = f.read(self.PART)
                        if len(chunk) == 0:
                            break
                        self.send(chunk)
                        self.log('Sent {} bytes...'.format(len(chunk)))
            else:
                self.rpc_send(RPCResponse(request.id, dict(code=1, message='No such index: {}'.format(index))))

    def main(self):
        while True:
            request = self.rpc_read()
            self.log(request)
            if request is None:
                self.log('[FileTransferPlugin] No more requests, stop handler')
                return
            if request.method == 'list':
                self.handle_list_shared(request)
            elif request.method == 'download':
                self.handle_download(request)
            elif request.method == 'upload':
                self.handle_upload(request)
