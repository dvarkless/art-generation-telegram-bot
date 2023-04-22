from pathlib import Path

import yaml

config_dir = Path(__file__).parent.parent.resolve() / "config"


class LoadConfig:
    def __init__(self, conf_name) -> None:
        config_dir = Path(__file__).parent.parent.resolve()
        self.path = config_dir / conf_name
        self.data = dict()

    def _load_all(self):
        with open(self.path, 'r') as f:
            items = yaml.safe_load(f)

        self.data = items

    def __getitem__(self, key):
        return self.data[key]

