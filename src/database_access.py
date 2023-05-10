import logging
import sqlite3 as sql
from pathlib import Path

from setup_handler import get_handler


class Database:
    __table_contents = {
        'id': 'INTEGER PRIMARY KEY',
        'action': 'VARCHAR(100)',
        'model': 'INTEGER',
        'prompt': 'VARCHAR(1000)',
        'orientation': 'INTEGER',
        'user': 'VARCHAR(70)',
        'user_id': 'INTEGER',
        'trigger_blacklist': 'INTEGER',
    }
    __relevant_actions = {
        'model': ['start', 'txt2img', 'img2img', 'set_model'],
        'prompt': ['start', 'txt2img', 'img2img'],
        'oriention': ['start', 'txt2img', 'img2img',
                      'change_orientation_mode'],
    }

    def __init__(self, path: str | Path, actions_txt_path: str | Path = ''):
        path_obj = Path(path)
        self.path = path

        self.logger = logging.getLogger(__name__)
        self.logger.addHandler(get_handler())
        self.logger.setLevel(logging.DEBUG)

        if not path_obj.exists():
            self.create_table()

        self.last_action = 'txt2img'
        self.last_model = 0
        self.last_orientation = 0
        self.last_prompt = ''
        self.last_gen_mode = 0
        self.is_blocked = False
        self.last_username = ''
        self.possible_actions = []

        self.all_actions = set()
        for actions in self.__relevant_actions.values():
            self.all_actions |= set(actions)
        self.all_actions = list(self.all_actions)

        if actions_txt_path != '':
            with open(actions_txt_path, 'r') as f:
                for line in f:
                    self.possible_actions.append(line.strip())

    def __enter__(self):
        self.con = sql.connect(self.path)
        self.cur = self.con.cursor()
        return self

    def __exit__(self, type, value, traceback):
        self.cur.close()
        if isinstance(value, Exception):
            self.con.rollback()
        else:
            self.con.commit()
        self.con.close()

    def create_table(self):
        query = 'CREATE TABLE main('
        for key, val in self.__table_contents.items():
            query += f'{key} {val},\n'
        query = query.rstrip(',\n')
        query += ');'
        self.con = sql.connect(self.path)
        self.cur = self.con.cursor()
        self.cur.execute(query)
        self.con.commit()
        self.con.close()
        self.logger.debug('Created table')

    def insert(self, action: str, user,
               model: int = -1, orientation: int = -1,
               prompt: str = '', blocked: bool = False):
        if isinstance(user, int):
            self.update_for_user(user)
            username = self.last_username
            user_id = user
        else:
            username = user.username
            user_id = user.id

        if self.possible_actions:
            assert action in self.possible_actions
        keys_to_add = list(self.__table_contents.keys())
        keys_to_add.remove('id')

        questions_len = len(self.__table_contents) - 1
        questions = '(' + ', '.join(['?' for _ in range(questions_len)]) + ');'

        query = 'INSERT INTO main ('
        query += ', \n'.join(keys_to_add)
        query += ')\nVALUES '
        query += questions
        self.last_query = query

        args = (
            action,
            model,
            prompt,
            orientation,
            username,
            user_id,
            int(blocked),
        )
        self.cur.execute(query, args)
        self.logger.debug(self.last_query.replace('?', '{}').format(*args))

    def check_user_exists(self, user):
        if isinstance(user, int):
            user_id = user
        else:
            user_id = user.id

        query = """
            SELECT COUNT(*) FROM main WHERE user_id = ?;
        """
        self.cur.execute(query, (user_id,))

        out = self.cur.fetchone()
        return bool(out)

    def update_for_user(self, user, update_only=None):
        self.logger.debug('Call: update_for_user')
        if update_only:
            assert update_only in self.__relevant_actions.keys()
        if isinstance(user, int):
            user_id = user
        else:
            user_id = user.id

        additional = ''
        actions_str = '('
        if update_only:
            actions_str += ','.join([f"'{m}'" for m in
                                    self.__relevant_actions[update_only]])
        else:
            actions_str += ','.join([f"'{m}'" for m in
                                    self.all_actions])

        actions_str += ')'
        additional = f'AND action IN {actions_str}'

        query = f"""
            SELECT * FROM main
            WHERE user_id=? {additional}
            ORDER BY id DESC;
        """
        self.cur.execute(query, (user_id,))
        out = self.cur.fetchone()

        if out:
            if not update_only:
                _, self.last_action, \
                    self.last_model, self.last_prompt, \
                    self.last_orientation, _, \
                    _, self.is_blocked = out
                self.is_blocked = True if self.is_blocked > 1 else False
            elif update_only == 'model':
                self.last_model = out[2]
            elif update_only == 'gen_mode':
                self.last_gen_mode = out[3]
            elif update_only == 'prompt':
                self.last_prompt = out[4]
            elif update_only == 'orientation':
                self.last_orientation = out[5]

        self.logger.debug(out)

    def select_all(self):
        query = "SELECT * FROM main"
        self.last_query = query
        self.cur.execute(query)
        while True:
            item = self.cur.fetchone()
            yield item if item is not None else StopIteration


if __name__ == '__main__':
    path = './info/db.db'

    class User:
        pass
    user = User()
    user.username = 'template'
    user.id = 101
    with Database(path) as db:
        db.insert("test", user)
        print(db.last_model)
