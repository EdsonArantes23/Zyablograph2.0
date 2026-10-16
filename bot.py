"""Run separately: python bot.py. One group, admin-only /digest, daily schedule (UTC offset)."""
import requests,time,re,logging,json,os
from datetime import datetime,timezone,timedelta
from engine5 import generate
import profiles
import core
from core import settings,connection,add_messages,get_messages,save_digest,update_published
logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
log=logging.getLogger('zyablograph')

def option_get(key,default):
 with connection() as db:
  result=db.execute('SELECT value FROM meta WHERE key=?',('admin_'+key,)).fetchone()
 return result[0] if result else str(default)

def option_set(key,value):
 with connection() as db:db.execute('INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)',('admin_'+key,str(value)))

def runtime_schedule(cfg):
 return option_get('digest_time',cfg.get('digest_time','12:00')),option_get('autodigest','on')=='on'

def admin_overview(cfg):
 when,enabled=runtime_schedule(cfg)
 return (f'⚙️ Зяблограф 5.5 — панель администратора\n'
         f'Автодайджест: {"включён" if enabled else "выключен"}\n'
         f'Время: {when} (UTC{int(cfg.get("timezone_offset",3)):+d})\n'
         f'Группа: {cfg.get("telegram_chat_id")}\n'
         f'Лимит: до {cfg.get("max_stories",10)} тем\n\n'
         '/schedule 18:00 — изменить время\n'
         '/autodigest on|off — автопубликация\n'
         '/digest — выпустить дайджест в группе сейчас\n'
         '/users — профили участников\n'
         '/profile ID — открыть профиль')

def tg(token,method,**kw):
 r=requests.post(f'https://api.telegram.org/bot{token}/{method}',json=kw,timeout=55)
 r.raise_for_status();j=r.json()
 if not j.get('ok'):raise RuntimeError(j.get('description','Telegram error'))
 return j['result']

def emit(token,chat,text,topic=None):
 # Telegram message limit 4096 chars; split safely by paragraph.
 chunks=[];current=''
 for part in text.split('\n'):
  if len(current)+len(part)+1>3700:
   if current:chunks.append(current);current=''
   while len(part)>3700:chunks.append(part[:3700]);part=part[3700:]
  current+=(('\n' if current else '')+part)
 if current:chunks.append(current)
 result=[]
 for part in chunks:
  args={'chat_id':chat,'text':part,'disable_web_page_preview':True}
  if topic:args['message_thread_id']=int(topic)
  result.append(tg(token,'sendMessage',**args)['message_id'])
 return result

def chat_id_of(m):return str(m['chat']['id'])
def admins(cfg):
 result=set()
 for item in str(cfg.get('admin_ids','')).split(','):
  try:result.add(int(item.strip()))
  except ValueError:pass
 return result

def extract_mention_ids(message):
 # Telegram MessageEntity type=text_mention carries an immutable user ID.
 result=[]
 for key,entities_key in (('text','entities'),('caption','caption_entities')):
  source=message.get(key) or ''
  for entity in message.get(entities_key) or []:
   if entity.get('type')!='text_mention':continue
   user=entity.get('user') or {}
   if not user.get('id'):continue
   # UTF-16 offsets do not map to Python string indices with emoji: label is optional.
   result.append({'telegram_id':str(user['id']),'label':'имя с прямым Telegram ID'})
 return result

def collect(m):
 chat=chat_id_of(m);u=m.get('from') or {}
 origin=m.get('forward_origin') or {};forward=''
 if origin:
  if origin.get('type')=='user':
   person=origin.get('sender_user',{});forward=' '.join(filter(None,[person.get('first_name'),person.get('last_name')]))
  elif origin.get('type')=='hidden_user':forward=origin.get('sender_user_name','скрытый автор')
  elif origin.get('type')=='channel':forward=origin.get('chat',{}).get('title','канал')
  elif origin.get('type')=='chat':forward=origin.get('sender_chat',{}).get('title','чат')
 author=' '.join(filter(None,[u.get('first_name'),u.get('last_name')])) or u.get('username') or m.get('sender_chat',{}).get('title','Неизвестно')
 body=m.get('text') or m.get('caption') or ('[Медиа без подписи]' if any(k in m for k in ('photo','video','document','voice','animation','sticker')) else '')
 if not body:return
 chat_url=f'https://t.me/c/{str(chat)[4:]}/{m["message_id"]}' if chat.startswith('-100') else ''
 row={'chat':chat,'mid':str(m['message_id']),'ts':datetime.fromtimestamp(m['date'],timezone.utc).replace(tzinfo=None).isoformat(timespec='seconds'),
      'author_id':str(u.get('id','')),'author':author,'body':body,'reply_to':str((m.get('reply_to_message') or {}).get('message_id','')),
      'forward_origin':forward,'topic':str(m.get('message_thread_id','')),'url':chat_url,
      'mentions':json.dumps(extract_mention_ids(m),ensure_ascii=False)}
 add_messages([row])

def run_digest(cfg,chat,thread=None):
 # UTC reference: Telegram timestamp stored as UTC naive
 rows=get_messages(chat,int(cfg['max_messages']),int(cfg['hours']),anchor=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec='seconds'))
 if len(rows)<3:return emit(cfg['telegram_token'],chat,'Для дайджеста пока недостаточно сообщений.',thread)
 body,usage=generate(chat,rows,cfg,progress=lambda s:log.info(s))
 did=save_digest(chat,body,rows)
 sent=emit(cfg['telegram_token'],chat,f'📰 ЗЯБЛОГРАФ · главное из {len(rows)} сообщений за последние {cfg["hours"]} ч.\n\n'+body,thread)
 update_published(did,','.join(map(str,sent)))
 log.info('Published digest %s to %s. Tokens: %s',did,chat,usage)
 return sent

def send_admin(token,admin,text,buttons=None):
    kw={'chat_id':admin,'text':text[:3800]}
    if buttons:kw['reply_markup']={'inline_keyboard':buttons}
    return tg(token,'sendMessage',**kw)

def profile_text(p):
    aliases=json.loads(p['aliases'] or '[]')
    return (f"🧾 Профиль #{p['id']}\nИмя: {p['display_name']}\nTelegram ID: {p['telegram_id'] or 'не привязан'}\n"
            f"Род: {p['grammar_gender']}\nUsername: @{p.get('username') or 'не указан'}\nПрозвища: {', '.join(aliases) or '—'}\n"
            f"Имя: {p.get('real_name') or '—'}; возраст: {p.get('age') if p.get('age') is not None else '—'}; город: {p.get('city') or '—'}\n"
            f"Публикация персональных полей: {'ДА' if p.get('publish_personal') else 'НЕТ'}\n"
            f"Описание: {p['description'] or '—'}\nНаблюдения из архива (не факт): {p['observed'] or '—'}\n"
            f"Биография: {p['bio'] or '—'}\nПубликация биографии: {'ДА' if p['publish_bio'] else 'НЕТ'}")

def profile_buttons(pid):
    return [[{'text':'♂ Мужской','callback_data':f'g:{pid}:male'},{'text':'♀ Женский','callback_data':f'g:{pid}:female'}],
            [{'text':'Нейтральный','callback_data':f'g:{pid}:neutral'},{'text':'Не указан','callback_data':f'g:{pid}:unknown'}],
            [{'text':'✅ Разрешить персональные данные','callback_data':f'q:{pid}:1'},{'text':'🚫 Скрыть персональные данные','callback_data':f'q:{pid}:0'}],
            [{'text':'✅ Разрешить биографию','callback_data':f'p:{pid}:1'},{'text':'🚫 Запретить','callback_data':f'p:{pid}:0'}]]

def admin_message(token,m,cfg):
    chat=m.get('chat') or {}; uid=str((m.get('from') or {}).get('id',''))
    if m.get('chat',{}).get('type','private')!='private' or int(uid or 0) not in admins(cfg):return
    text=(m.get('text') or '').strip()
    if not text:return
    cmd,_,args=text.partition(' ');cmd=cmd.split('@')[0].lower();args=args.strip()
    if cmd in ('/admin','/settings'):
        send_admin(token,chat['id'],admin_overview(cfg));return
    if cmd=='/schedule':
        if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',args):
            send_admin(token,chat['id'],'Формат: /schedule 18:00 (время сервера с указанным смещением TIMEZONE_OFFSET)');return
        option_set('digest_time',args)
        send_admin(token,chat['id'],'✅ Время ежедневного выпуска: '+args);return
    if cmd=='/autodigest':
        if args not in ('on','off'):send_admin(token,chat['id'],'Формат: /autodigest on или /autodigest off');return
        option_set('autodigest',args);send_admin(token,chat['id'],'✅ Автодайджест: '+args);return
    if cmd=='/digest':
        send_admin(token,chat['id'],'⏳ Запускаю дайджест для группы. Результат будет опубликован туда.');run_digest(cfg,str(cfg['telegram_chat_id']));return
    if cmd in ('/start','/help','/users'):
        records=profiles.list_profiles(limit=12)
        lines=['🧾 Зяблограф 5.5 — управление участниками','/users [поиск] — поиск и последние профили','/profile ID — открыть карточку','/adduser Имя — создать профиль','/gender ID male|female|neutral|unknown','/alias ID прозвище1, прозвище2','/username ID @username — связать тег','/description ID текст','/bio ID текст','/publish ID on|off — разрешить биографию в дайджесте','/link ID TelegramUserID — привязать ID','/notes ID текст — заметка, в дайджест не попадает','/setname ID имя; /setage ID возраст; /setcity ID город','/personal ID on|off — разрешить личные поля','/admin — расписание и управление','/importprofiles — импорт черновиков JSON с сервера','', 'Последние профили:']
        if cmd=='/users' and args:
            records=profiles.list_profiles(args,limit=20);lines=['Результаты поиска «'+args[:70]+'»:']
        lines += [f"#{p['id']} {p['display_name']} ({p['telegram_id'] or 'без ID'})" for p in records]
        send_admin(token,chat['id'],'\n'.join(lines));return
    if cmd=='/adduser':
        if not args:send_admin(token,chat['id'],'Пример: /adduser SuperZyabla');return
        pid=profiles.create(args);send_admin(token,chat['id'],f'Профиль создан: #{pid}. Открой /profile {pid}');return
    if cmd=='/importprofiles':
        path=core.ROOT/'profiles_import.json'
        if not path.exists():send_admin(token,chat['id'],'Не найден profiles_import.json рядом с bot.py. Помести файл черновиков на сервер и повтори команду.');return
        count=profiles.import_drafts(path);send_admin(token,chat['id'],f'Импортировано {count} черновиков. Сведения без проверки не считаются биографией.');return
    pidstr,_,value=args.partition(' ')
    if cmd=='/profile':
        if not pidstr.isdigit():send_admin(token,chat['id'],'Пример: /profile 12');return
        p=profiles.by_id(pidstr)
        send_admin(token,chat['id'],profile_text(p) if p else 'Профиль не найден',profile_buttons(int(pidstr)) if p else None);return
    fields={'/setname':'real_name','/setage':'age','/setcity':'city','/personal':'publish_personal','/gender':'grammar_gender','/alias':'aliases','/description':'description','/bio':'bio','/publish':'publish_bio','/link':'telegram_id','/notes':'notes','/username':'username'}
    if cmd in fields:
        if not pidstr.isdigit() or not value:send_admin(token,chat['id'],f'Формат: {cmd} ID значение');return
        if cmd in ('/publish','/personal'):value='1' if value.lower() in ('on','yes','да','1') else '0'
        profiles.set_field(pidstr,fields[cmd],value)
        send_admin(token,chat['id'],profile_text(profiles.by_id(pidstr)),profile_buttons(int(pidstr)));return
    send_admin(token,chat['id'],'Неизвестная команда. Отправь /users')

def main():
 cfg=settings();token=cfg.get('telegram_token','').strip();wanted=str(cfg.get('telegram_chat_id','')).strip()
 if not token or not wanted:raise SystemExit('Укажи TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID в переменных ENV Bothost')
 if not admins(cfg):raise SystemExit('Укажи ADMIN_IDS в переменных окружения')
 me=tg(token,'getMe');log.info('Bot @%s',me['username'])
 with connection() as db:
  saved=db.execute('SELECT value FROM meta WHERE key="telegram_offset"').fetchone();offset=int(saved[0]) if saved else 0
 tz=timezone(timedelta(hours=int(cfg.get('timezone_offset',3))))
 while True:
  try:
   cfg=settings()
   updates=tg(token,'getUpdates',offset=offset,timeout=35,allowed_updates=['message','edited_message','callback_query'])
   for update in updates:
    offset=max(offset,int(update['update_id'])+1)
    with connection() as db:db.execute('INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)',('telegram_offset',str(offset)))
    cb=update.get('callback_query')
    if cb:
     uid=(cb.get('from') or {}).get('id');data=cb.get('data','');cid=(cb.get('message') or {}).get('chat',{}).get('id')
     try:
      if uid in admins(cfg) and str(cid)==str(uid):
       typ,pid,val=data.split(':',2)
       if typ in ('g','p','q'):
        profiles.set_field(pid,'grammar_gender' if typ=='g' else ('publish_bio' if typ=='p' else 'publish_personal'),val)
        p=profiles.by_id(pid)
        send_admin(token,cid,'✅ Изменения сохранены\n'+profile_text(p),profile_buttons(int(pid)))
     except Exception as e:log.exception('Profile button failed')
     finally:tg(token,'answerCallbackQuery',callback_query_id=cb['id'])
     continue
    m=update.get('message') or update.get('edited_message') or {}
    if not m:continue
    uid=(m.get('from') or {}).get('id');chat_id=chat_id_of(m)
    if m.get('chat',{}).get('type')=='private':
     if uid in admins(cfg) and 'edited_message' not in update:
      try:admin_message(token,m,cfg)
      except Exception as e:log.exception('Profile command failed');send_admin(token,chat_id,'Ошибка: '+str(e)[:300])
     continue
    if chat_id!=wanted:continue
    u=m.get('from') or {}
    if u.get('id'):
     name=' '.join(filter(None,[u.get('first_name'),u.get('last_name')])) or u.get('username') or 'Неизвестно'
     profiles.identify(u['id'],name,u.get('username',''))
    # Explicit text_mention has a real Telegram user object, not a guessed @handle.
    for entities in (m.get('entities') or [], m.get('caption_entities') or []):
     for entity in entities:
      if entity.get('type')=='text_mention' and (entity.get('user') or {}).get('id'):
       mentioned=entity['user']
       visible_name=' '.join(filter(None,[mentioned.get('first_name'),mentioned.get('last_name')])) or mentioned.get('username') or 'Неизвестно'
       profiles.identify(mentioned['id'],visible_name,mentioned.get('username',''))
    if 'edited_message' not in update:collect(m)
    msgtext=(m.get('text') or '').strip()
    if re.match(r'^/digest(?:@\w+)?(?:\s|$)',msgtext,re.I):
     if uid not in admins(cfg):continue
     try:run_digest(cfg,wanted,m.get('message_thread_id'))
     except Exception as e:log.exception('Digest failed');emit(token,wanted,'Не удалось создать дайджест: '+str(e)[:400],m.get('message_thread_id'))
   now=datetime.now(tz);key=now.strftime('%Y-%m-%d');HHMM=now.strftime('%H:%M');due,auto_enabled=runtime_schedule(cfg)
   if auto_enabled and HHMM>=due:
    with connection() as db:
     k='last_auto_'+wanted;r=db.execute('SELECT value FROM meta WHERE key=?',(k,)).fetchone();already=r and r[0]==key
    if not already:
     try:
      run_digest(cfg,wanted)
      with connection() as db:db.execute('INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)',(k,key))
     except Exception:log.exception('Scheduled digest failed')
  except KeyboardInterrupt:break
  except Exception:log.exception('Polling problem');time.sleep(10)
if __name__=='__main__':main()
