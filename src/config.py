import logging
from pathlib import Path

import yaml

from setup_handler import get_handler

logger = logging.getLogger(__name__)

# add handler to logger
logger.addHandler(get_handler())


class LoadConfig:
    def __init__(self, conf_path) -> None:
        self.path = Path(conf_path)
        assert self.path.exists()
        self.data = dict()

        self._load_all()

    def _load_all(self):
        with open(self.path, 'r') as f:
            items = yaml.safe_load(f)
        if not items:
            raise FileNotFoundError(f'The requested config file \
                                    "{self.path}" is empty')
        self.data = items.copy()

    def __getitem__(self, key):
        return self.data[key]

    def items(self):
        for data_tup in self.data.items():
            yield data_tup

    def values(self):
        for data in self.data.values():
            yield data


class SecretsAccess:
    __filenames = {
        'whitelist': 'whitelist.txt',
        'blacklist': 'blacklist.txt',
        'token': 'tg_token.txt',
        'ban_words': 'word_blacklist.txt',
        'actions': 'possible_actions.txt',
    }

    def __init__(self, secrets_dir: str | Path = './info') -> None:
        self.path = Path(secrets_dir).resolve()
        self.data = dict()
        self._load_all()

    def _load_all(self):
        for meaning, filename in self.__filenames.items():
            file_path = self.path / filename
            if not file_path.exists():
                file_path.touch()
                self.warn(meaning)

            if meaning != 'token':
                self.data[meaning] = []
            else:
                self.data[meaning] = 0
            with open(file_path, 'r') as f:
                for line in f:
                    line = line.strip()
                    if meaning != 'token':
                        self.data[meaning].append(line)
                    else:
                        self.data[meaning] = line
                        break

    def __getitem__(self, key):
        return self.data[key]

    def insert_item(self, meaning, value):
        if meaning in ['blacklist', 'whitelist']:
            file_path = self.path / self.__filenames[meaning]
            with open(file_path, 'a') as f:
                f.write(value)

            self.data[meaning].append(value)
        else:
            raise KeyError(f'Could not edit this key: {meaning}')

    def get_token(self):
        return self['token']

    def get_whitelist(self):
        return self['whitelist']

    def get_blacklist(self):
        return self['blacklist']

    def get_banwords(self):
        return self['ban_words']

    def get_actions(self):
        return self['actions']

    def warn(self, about):
        print(f'Warning: creating a new blank {about} file, \
                since {self.path / self.__filenames[about]} not exists')
