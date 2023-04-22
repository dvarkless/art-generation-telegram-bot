import sqlite3 as sql
from pathlib import Path


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

    def __init__(self, path: str):
        path_obj = Path(path)
        self.path = path
        if not path_obj.exists():
            self.create_table()

        self.last_action = 'txt2img'
        self.last_model = 1
        self.last_orientation = 0
        self.is_blocked = False

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

    def insert(self, action: str, model: int,
               user: User, orientation: int,
               prompt: str = '', blocked: bool = False):
        username = user.username
        user_id = user.id

        query = 'INSERT INTO main ('
        questions = '('
        for key in self.__table_contents.keys():
            if key == 'id':
                continue
            query += f'{key},\n'
            questions += '?, '
        query = query.rstrip(',\n')
        questions = questions.rstrip(', ')
        query += ')\nVALUES '
        questions += ');'
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

    def update_for_user(self, user):
        user_id = user.id

        query = f"""
            SELECT * FROM main
            WHERE user_id={user_id}
            ORDER BY id DESC;
        """
        self.cur.execute(query)
        out = self.cur.fetchone()
        if out:
            self.last_action = out[1]
            self.last_model = out[2]
            self.last_orientation = out[4]
            self.is_blocked = True if out[7] > 1 else False

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
        # db.insert('iii2img', 0, user, 0, 'create asd smth', False)
        db.update_for_user(user)
        print(db.last_model)
