"""Verified/admin-editable user profiles; SQLite persists independently of repository deployments."""
import json, re, sqlite3
from collections import defaultdict
from pathlib import Path
import core

SCHEMA="""CREATE TABLE IF NOT EXISTS user_profiles(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  telegram_id TEXT UNIQUE,
  display_name TEXT NOT NULL,
  aliases TEXT NOT NULL DEFAULT '[]',
  username TEXT NOT NULL DEFAULT '',
  grammar_gender TEXT NOT NULL DEFAULT 'unknown',
  description TEXT NOT NULL DEFAULT '',
  bio TEXT NOT NULL DEFAULT '',
  publish_bio INTEGER NOT NULL DEFAULT 0,
  observed TEXT NOT NULL DEFAULT '',
  notes TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT 'telegram',
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS profiles_name ON user_profiles(display_name COLLATE NOCASE);
"""

def db():
    c=core.connection();c.executescript(SCHEMA);c.row_factory=sqlite3.Row
    # Migration for databases created by 5.1.
    columns={r[1] for r in c.execute('PRAGMA table_info(user_profiles)')}
    if 'username' not in columns:
        c.execute("ALTER TABLE user_profiles ADD COLUMN username TEXT NOT NULL DEFAULT ''")
    for field,definition in {'real_name':"TEXT NOT NULL DEFAULT ''",'age':"INTEGER",'city':"TEXT NOT NULL DEFAULT ''",'publish_personal':"INTEGER NOT NULL DEFAULT 0"}.items():
        if field not in {r[1] for r in c.execute('PRAGMA table_info(user_profiles)')}:
            c.execute(f'ALTER TABLE user_profiles ADD COLUMN {field} {definition}')
    return c

def clean(v,n=700):return str(v or '').strip()[:n]
def by_id(uid):
    with db() as c:
        row=c.execute('SELECT * FROM user_profiles WHERE id=?',(int(uid),)).fetchone()
    return dict(row) if row else None

def identify(tg_id,name,username=None):
    """Auto-create ONLY for actual Telegram user IDs; never merge by name silently."""
    if not tg_id:return None
    with db() as c:
        row=c.execute('SELECT id FROM user_profiles WHERE telegram_id=?',(str(tg_id),)).fetchone()
        handle=normalize_handle(username)
        if row:
            if name:c.execute('UPDATE user_profiles SET display_name=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(clean(name,150),row['id']))
            if username is not None:
                if handle:
                    c.execute('UPDATE user_profiles SET username="" WHERE lower(username)=? AND id<>?',(handle,int(row['id'])))
                c.execute('UPDATE user_profiles SET username=? WHERE id=?',(handle,int(row['id'])))
            return int(row['id'])
        if handle:
            c.execute('UPDATE user_profiles SET username="" WHERE lower(username)=?',(handle,))
        cur=c.execute('INSERT INTO user_profiles(telegram_id,display_name,username) VALUES(?,?,?)',(str(tg_id),clean(name,150) or 'Неизвестный',handle))
        return cur.lastrowid

def create(name):
    with db() as c:
        return c.execute('INSERT INTO user_profiles(display_name,source) VALUES(?,?)',(clean(name,150),'admin')).lastrowid

def list_profiles(search='',limit=20):
    with db() as c:
        if search:
            term='%'+search[:70]+'%'
            rows=c.execute('SELECT * FROM user_profiles WHERE display_name LIKE ? OR aliases LIKE ? OR telegram_id LIKE ? OR username LIKE ? ORDER BY telegram_id IS NULL, id DESC LIMIT ?', (term,term,term,term,limit)).fetchall()
        else:rows=c.execute('SELECT * FROM user_profiles ORDER BY telegram_id IS NULL, id DESC LIMIT ?', (limit,)).fetchall()
    return [dict(r) for r in rows]

def set_field(pid,field,value):
    columns={'grammar_gender','aliases','description','bio','publish_bio','observed','notes','telegram_id','username','real_name','age','city','publish_personal'}
    if field not in columns:raise ValueError('Поле не поддерживается')
    if field=='grammar_gender' and value not in ('male','female','neutral','unknown'):raise ValueError('Пол: male/female/neutral/unknown')
    if field in ('publish_bio','publish_personal'):value=1 if str(value).lower() in ('1','yes','true','да','on') else 0
    if field=='age':
        if str(value).strip().lower() in ('-','none','нет'):value=None
        elif not str(value).strip().isdigit() or not 0 <= int(value) <= 120:raise ValueError('Возраст должен быть числом от 0 до 120')
        else:value=int(value)
    if field in ('real_name','city'):value=clean(value,120)
    if field=='aliases':value=json.dumps([v.strip() for v in re.split('[,;]',str(value)) if v.strip()][:20],ensure_ascii=False)
    if field=='username':
        value=normalize_handle(value)
    if field=='telegram_id':
        if value and not str(value).isdigit():raise ValueError('Telegram ID должен быть числом')
        value=str(value) if value else None
    if field in ('description','bio','observed','notes'):value=clean(value,1400)
    with db() as c:
        if field=='username' and value:
            other=c.execute('SELECT id FROM user_profiles WHERE lower(username)=? AND id<>?',(value,int(pid))).fetchone()
            if other:raise ValueError('Этот @username уже присвоен другому профилю #'+str(other['id']))
        cur=c.execute(f'UPDATE user_profiles SET {field}=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(value,int(pid)))
        if not cur.rowcount:raise ValueError('Профиль не найден')

def import_drafts(path):
    obj=json.loads(Path(path).read_text(encoding='utf-8'))
    items=obj.get('participants',[]) if isinstance(obj,dict) else obj
    inserted=0
    with db() as c:
        for row in items:
            name=clean(row.get('display_name'),150)
            if not name:continue
            # Avoid dangerous implicit name-based fusion: no Telegram ID => draft only.
            existing=c.execute('SELECT id FROM user_profiles WHERE display_name=? AND telegram_id IS NULL',(name,)).fetchone()
            if existing:continue
            c.execute('INSERT INTO user_profiles(display_name,aliases,grammar_gender,description,bio,publish_bio,observed,source) VALUES(?,?,?,?,?,?,?,?)',(
              name,json.dumps(row.get('aliases',[]),ensure_ascii=False),row.get('grammar_gender','unknown') if row.get('grammar_gender') in ('male','female','neutral','unknown') else 'unknown',
              '',clean(row.get('bio_confirmed'),1400) if row.get('profile_status')=='confirmed' else '',
              int(bool(row.get('publish_bio'))) if row.get('profile_status')=='confirmed' else 0,
              clean(row.get('chat_persona_observed'),900),'html_draft'))
            inserted+=1
    return inserted

def _aliases(profile):
    try:return json.loads(profile.get('aliases') or '[]')
    except ValueError:return []

HANDLE_RE=re.compile(r'(?<![\w@])@([A-Za-z][A-Za-z0-9_]{4,31})(?![\w])')

def normalize_handle(value):
    handle=str(value or '').strip().lstrip('@').lower()
    if not handle:return ''
    if not re.fullmatch(r'[a-z][a-z0-9_]{4,31}',handle):
        raise ValueError('Username Telegram: 5–32 латинских букв, цифр и _, начиная с буквы')
    return handle

def _pool():
    with db() as c:return [dict(r) for r in c.execute('SELECT * FROM user_profiles')]

def _unique_handles(pool):
    by_handle=defaultdict(list)
    for p in pool:
        if p.get('username'):by_handle[p['username'].casefold()].append(p)
    return {key:values[0] for key,values in by_handle.items() if len(values)==1}

def mention_context(rows, max_chars=3500):
    """Resolve explicit @mentions by active handle; do not equate a mention with authorship."""
    pool=_pool();handles=_unique_handles(pool)
    by_id={str(p['telegram_id']):p for p in pool if p.get('telegram_id')}
    results=[];seen=set()
    for row in rows:
        text=str(row.get('body') or '')
        mentioned=[]
        for matched in HANDLE_RE.finditer(text):
            handle=matched.group(1).casefold()
            person=handles.get(handle)
            if not person:continue
            unique=(str(row.get('mid','')),handle)
            if unique in seen:continue
            seen.add(unique)
            mentioned.append((handle,person))
        # Bot API text_mention: direct user ID even without @username.
        try:entities=json.loads(row.get('mentions') or '[]')
        except (ValueError,TypeError):entities=[]
        for item in entities:
            uid=str(item.get('telegram_id') or '') if isinstance(item,dict) else ''
            person=by_id.get(uid)
            if person and all(p['id']!=person['id'] for _,p in mentioned):
                mentioned.append((str(item.get('label') or 'текстовое упоминание').lstrip('@'),person))
        for handle,p in mentioned:
            bits=[f"Сообщение #{row.get('mid')}: {('@'+handle) if handle!='текстовое упоминание' else 'текстовое упоминание'} — упомянут участник профиля #{p['id']} ({p['display_name']})"]
            if p.get('grammar_gender')!='unknown':bits.append('род: '+p['grammar_gender'])
            if _aliases(p):bits.append('прозвища: '+', '.join(_aliases(p)[:4]))
            results.append('; '.join(bits))
    return '\n'.join(results)[:max_chars]

def context(rows, max_chars=3500):
    """Only unique author profiles + explicitly tagged profiles."""
    pool=_pool();handles=_unique_handles(pool)
    ids={str(r.get('author_id')) for r in rows if r.get('author_id')}
    names={str(r.get('author','')).casefold() for r in rows}
    names_count=defaultdict(set)
    for p in pool:
        for name in [p['display_name']]+_aliases(p):
            names_count[name.casefold()].add(p['id'])
    selected_ids=set()
    for p in pool:
        if p['telegram_id'] and str(p['telegram_id']) in ids:
            selected_ids.add(p['id'])
        # HTML export authors have no IDs: unique explicit name matches only.
        elif not p['telegram_id'] and any(n.casefold() in names and len(names_count[n.casefold()])==1 for n in [p['display_name']]+_aliases(p)):
            selected_ids.add(p['id'])
    # @username in message text is about the tagged person, not the author.
    for row in rows:
        for m in HANDLE_RE.finditer(str(row.get('body') or '')):
            p=handles.get(m.group(1).casefold())
            if p:selected_ids.add(p['id'])
        try:entities=json.loads(row.get('mentions') or '[]')
        except (ValueError,TypeError):entities=[]
        for item in entities:
            if not isinstance(item,dict):continue
            for p in pool:
                if p['telegram_id'] and p['telegram_id']==str(item.get('telegram_id')):selected_ids.add(p['id'])
    selected=[]
    for p in pool:
        if p['id'] not in selected_ids:continue
        bits=[f"Профиль #{p['id']} — {p['display_name']}"]
        if p.get('username'):bits.append('Telegram @'+p['username'])
        if p['grammar_gender']!='unknown':bits.append('род: '+p['grammar_gender'])
        if _aliases(p):bits.append('прозвища: '+', '.join(_aliases(p)[:7]))
        if p.get('publish_personal'):
            if p.get('real_name'):bits.append('имя (администратор): '+p['real_name'])
            if p.get('age') is not None:bits.append('возраст по данным администратора (может устареть): '+str(p['age']))
            if p.get('city'):bits.append('город (администратор): '+p['city'])
        if p['description']:bits.append('описание администратора (НЕ доказательство): '+p['description'])
        if p['observed']:bits.append('наблюдение из архива, НЕ факт: '+p['observed'][:320])
        if p['bio'] and p['publish_bio']:bits.append('подтверждённая администратором публикабельная биография: '+p['bio'][:450])
        selected.append('; '.join(bits))
    return '\n'.join(selected)[:max_chars]
