import asyncio, random, logging, re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.error import BadRequest
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# إعداد السجلات (Log) لمعرفة الأخطاء فوراً
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- 1. الإعدادات وقواعد البيانات ---
TOKEN = "8681015207:AAEI4KCFDc6QqPMAC4bflGVu1dx9XvF7oT0" # ضع التوكن هنا
OWNER_ID = 7083077757 

database = { OWNER_ID: {"username": "@owner", "channel_id": None, "game": None} }
channel_to_admin = {} 
global_active_players = {} 

def get_empty_game():
    return {
        "is_registration_open": False, "players": {}, "waiting_for_name": set(), 
        "is_game_started": False, "current_turn": None, 
        "turn_timer_task": None, "required_eliminations": 0, "current_eliminations": 0,
        "timer_msg_id": None, 
        "counter_msg_id": None 
    }

# --- 2. نظام الإلحاح التصاعدي (تم التحديث لـ 4 محاولات ووقت تصاعدي) ---
async def safe_send(context, chat_id, text, reply_markup=None):
    for attempt in range(4): # 4 محاولات بدلاً من 3
        try:
            return await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
        except Exception as e:
            if attempt == 3: return None # إذا فشل في الرابعة يتجاهل
            await asyncio.sleep(attempt + 1) # انتظار تصاعدي: 1ث، 2ث، 3ث

async def safe_edit(context, chat_id, message_id, text, reply_markup=None):
    for attempt in range(4): # 4 محاولات بدلاً من 3
        try:
            return await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, reply_markup=reply_markup)
        except BadRequest as e:
            if "not modified" in str(e).lower(): break # تجاهل إذا كان النص لم يتغير
            if attempt == 3: return None
            await asyncio.sleep(attempt + 1) # انتظار تصاعدي: 1ث، 2ث، 3ث
        except Exception:
            if attempt == 3: return None
            await asyncio.sleep(attempt + 1) # انتظار تصاعدي: 1ث، 2ث، 3ث

# --- 3. لوحات التحكم ---
def get_owner_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("➕ إضافة مشرف"), KeyboardButton("➖ إزالة مشرف")],
        [KeyboardButton("📋 قائمة المشرفين"), KeyboardButton("📡 ربط القناة")],
        [KeyboardButton("🎮 واجهة المشرف")]
    ], resize_keyboard=True)

def get_admin_keyboard(is_owner=False):
    buttons = [
        [KeyboardButton("🔓 فتح باب التسجيل"), KeyboardButton("🚀 ابدأ الملحمة")],
        [KeyboardButton("🎡 تدوير الروليت"), KeyboardButton("⏹️ إيقاف اللعبة")],
        [KeyboardButton("📡 ربط القناة")]
    ]
    if is_owner: buttons.append([KeyboardButton("🔙 العودة للمالك")])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

# --- 4. محرك اللعبة والعدادات ---
async def turn_timer_logic(admin_id, player_id, context):
    game = database[admin_id].get("game")
    channel_id = database[admin_id].get("channel_id")
    
    # التعديل الجديد: إرسال رسالة البداية مرة واحدة فقط بدون أي تعديل لاحق لتجنب القيود
    await safe_send(context, chat_id=channel_id, text="⏳ حان وقت التنفيذ! لديك 60 ثانية لإنجاز المهمة وإلا سيتم إقصاؤك.")

    # ننتظر 60 ثانية (مقسمة لثانية واحدة لمراقبة إذا أنهى اللاعب المهمة مبكراً)
    for _ in range(60):
        await asyncio.sleep(1)
        # إذا اللعبة توقفت أو اللاعب أنهى مهمته وتغير الدور، نخرج بصمت
        if not game or not game.get("is_game_started") or game.get("current_turn") != player_id:
            return 
    
    # منطقة الصفر (انتهت الـ 60 ثانية ولم يُنهِ اللاعب المطلوب)
    if game and game.get("current_turn") == player_id:
        game["current_turn"] = None

        player_name = "لاعب"
        if player_id in game["players"]:
            player_name = game["players"].pop(player_id)["name"]
            if player_id in global_active_players: 
                del global_active_players[player_id]

        await safe_send(context, chat_id=player_id, text="⏰ انتهى الوقت المسموح لك! لقد تم إقصاؤك من الساحة لعدم إنجاز المهمة.\n\n💡 اطلب من الادمن بدء اللعبة من جديد او انتظر حتى ينتهي الدور والمشاركة فالرابط مرة اخرى.")
            
        await safe_send(context, chat_id=channel_id, text=f"⌛ نفد الوقت! تم إقصاء صاحب الدور ✨ {player_name} ✨ لعدم إنجازه المهمة.")

        is_winner = await check_winner(admin_id, context)
        if not is_winner:
            await safe_send(context, chat_id=admin_id, text="⚠️ تم طرد صاحب الدور لنفاد الوقت. اللعبة مستمرة، القائمة محدثة أدناه، اضغط على (🎡 تدوير الروليت) لاختيار اللاعب التالي!")
            await send_admin_summary(admin_id, context)

async def check_winner(admin_id, context):
    game = database[admin_id].get("game")
    if game and len(game.get("players", {})) == 1:
        task = game.get("turn_timer_task")
        try:
            if task and not task.done() and task != asyncio.current_task():
                task.cancel()
        except Exception: pass
        
        winner_id = list(game["players"].keys())[0]
        name = game["players"][winner_id]["name"]
        user_handle = game["players"][winner_id].get("user", "")
        
        await safe_send(context, chat_id=database[admin_id]["channel_id"], text=f"🎉 انتهت الملحمة! الناجي الأخير والمتوج باللقب هو: 👑 {name} ({user_handle}) 👑")
        
        win_msg = "🎉 ألف مبروك! لقد سحقت الجميع وكنت الناجي الأخير! أنت بطل الملحمة 🏆\n\n💡 اطلب من الادمن بدء اللعبة من جديد او انتظر حتى ينتهي الدور والمشاركة فالرابط مرة اخرى."
        await safe_send(context, chat_id=winner_id, text=win_msg)
        
        if winner_id in global_active_players: del global_active_players[winner_id]
        
        admin_msg = "✅ انتهت الملحمة وتم تتويج البطل آلياً!\n\n💡 اللعبة الآن مغلقة بالكامل. لبدء جولة جديدة، قم بالضغط على (🔓 فتح باب التسجيل) من جديد."
        await safe_send(context, chat_id=admin_id, text=admin_msg)

        database[admin_id]["game"] = None 
        return True
    return False

async def send_admin_summary(admin_id, context):
    game = database[admin_id].get("game")
    if not game or not game.get("players"): return
    p_list = "\n".join([f"👤 {d['name']} | الحساب: {d.get('user', 'مجهول')}" for d in game["players"].values()])
    text = f"📋 قائمة الصامدين في ساحة المعركة:\n\n{p_list}"
    kbd = [[InlineKeyboardButton("🛠️ طرد إداري غامض", callback_data=f"kickmenu_{admin_id}")],
           [InlineKeyboardButton("🔄 تحديث القائمة", callback_data=f"refresh_{admin_id}")]]
    await safe_send(context, chat_id=admin_id, text=text, reply_markup=InlineKeyboardMarkup(kbd))

async def start_turn(admin_id, context):
    game = database[admin_id].get("game")
    if not game: return
    uid = random.choice(list(game["players"].keys()))
    game["current_turn"], game["current_eliminations"] = uid, 0
    
    player_name = game['players'][uid]['name']
    req_elim = game['required_eliminations']
    
    msg_channel = f"🎡 دارت عجلة القدر وتوقفت عند: ✨ {player_name} ✨\n🔥 مطلوب منه استبعاد {req_elim} من اللاعبين خلال 60 ثانية!"
    await safe_send(context, chat_id=database[admin_id]["channel_id"], text=msg_channel)
    await safe_send(context, chat_id=admin_id, text=f"✅ تم الإعلان في القناة: بدأ دور {player_name} ومطلوب منه {req_elim} استبعادات.")

    if game.get("turn_timer_task"): 
        try:
            if not game["turn_timer_task"].done() and game["turn_timer_task"] != asyncio.current_task():
                game["turn_timer_task"].cancel()
        except Exception: pass
        
    game["turn_timer_task"] = asyncio.create_task(turn_timer_logic(admin_id, uid, context))
    await send_player_menu(admin_id, uid, context)

async def send_player_menu(admin_id, uid, context):
    game = database[admin_id].get("game")
    if not game: return
    count = game["required_eliminations"] - game["current_eliminations"]
    msg = f"🗡️ حان وقت الحسم! متبقي لك استبعاد {count} لاعبين. اختر بحكمة:"
    kbd = [[InlineKeyboardButton(f"❌ {d['name']}", callback_data=f"out_{admin_id}_{p}")] for p, d in game["players"].items() if p != uid]
    
    if game["players"][uid].get("has_reveal"):
        kbd.insert(0, [InlineKeyboardButton("🧐 كشف المستور (محاولة واحدة)", callback_data=f"rev_{admin_id}")])
    else:
        kbd.insert(0, [InlineKeyboardButton("🚫 انتهت محاولات الكشف", callback_data="none")])
        
    await safe_send(context, chat_id=uid, text=msg, reply_markup=InlineKeyboardMarkup(kbd))

# --- 5. العقل المدبر: معالجة الرسائل ---
async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message: return
    user_id = update.effective_user.id
    text = update.message.text or ""
    
    u_name = update.effective_user.username
    current_username = f"@{u_name.lower()}" if u_name else None
    real_name = update.effective_user.first_name 
    display_user = current_username if current_username else real_name 

    # 1. أولوية تسجيل الأسماء مع فحص الطول (حارس الأسماء)
    for aid, data in database.items():
        if isinstance(aid, (int, str)) and data.get("game"):
            if user_id in data["game"]["waiting_for_name"]:
                if len(text) > 40:
                    await update.message.reply_text("⚠️ عذراً، الاسم طويل جداً! (أقصى حد هو 40 حرف).\nرجاءً أرسل اسماً أقصر لتدخل الساحة:")
                    return

                data["game"]["players"][user_id] = {"name": text, "user": display_user, "has_reveal": True}
                global_active_players[user_id] = aid 
                data["game"]["waiting_for_name"].remove(user_id)
                await update.message.reply_text(f"✅ تم تسجيل دخولك لساحة المعركة باسم: {text}\nانتظر بدء الملحمة!")
                
                count = len(data["game"]["players"])
                channel_chat = database[aid].get("channel_id")
                msg_id = data["game"].get("counter_msg_id")
                
                # التعديل الجديد: تحديث العداد كل 5 لاعبين فقط لتخفيف الضغط على البوت وتجنب حظر تليجرام
                if channel_chat and msg_id and count % 5 == 0:
                    await safe_edit(context, chat_id=channel_chat, message_id=msg_id, text=f"👥 عدد المشتركين الحالي: {count}")
                return

    admin_key = None
    if user_id == OWNER_ID:
        admin_key = OWNER_ID
    else:
        for key, value in list(database.items()):
            if current_username and current_username == str(value.get("username", "")).lower():
                if key != user_id:
                    database[user_id] = database.pop(key)
                    database[user_id]["username"] = current_username
                    for c_id, a_id in channel_to_admin.items():
                        if a_id == key: channel_to_admin[c_id] = user_id
                admin_key = user_id
                break
                
    is_admin = admin_key is not None
    is_owner = (user_id == OWNER_ID)

    if is_admin:
        if "واجهة" in text:
            await update.message.reply_text("🕹️ واجهة التحكم جاهزة بين يديك:", reply_markup=get_admin_keyboard(is_owner)); return
        elif "العودة للمالك" in text:
            await update.message.reply_text("🔙 أهلاً بك في القيادة العليا (واجهة المالك):", reply_markup=get_owner_keyboard()); return
        elif "إضافة مشرف" in text and is_owner:
            await update.message.reply_text("➕ أرسل يوزرات المشرفين الجدد (يمكنك إرسال أكثر من يوزر في رسالة واحدة):"); context.user_data["action"] = "add_admin"; return
        elif "إزالة مشرف" in text and is_owner:
            await update.message.reply_text("➖ أرسل يوزر المشرف لطرده من الإدارة:"); context.user_data["action"] = "rem_admin"; return
        
        elif "قائمة المشرفين" in text and is_owner:
            admin_list = [v.get('username') for k, v in database.items() if k != OWNER_ID and v.get('username')]
            if admin_list:
                text_msg = "📋 قائمة المشرفين المعينين حالياً:\n\n" + "\n".join([f"🔹 {username}" for username in admin_list])
                await update.message.reply_text(text_msg)
            else:
                await update.message.reply_text("📋 لا يوجد أي مشرفين مضافين حالياً.")
            return

        elif "فتح باب التسجيل" in text:
            existing_game = database[admin_key].get("game")
            if existing_game and (existing_game.get("is_game_started") or existing_game.get("is_registration_open")):
                await update.message.reply_text("🚫 لا يمكنك فتح باب التسجيل الآن! هناك تسجيل مفتوح بالفعل أو معركة جارية."); return
            
            c_id = database[admin_key].get("channel_id")
            if not c_id: await update.message.reply_text("⚠️ يرجى ربط القناة أولاً لتتمكن من فتح التسجيل!"); return
            
            database[admin_key]["game"] = get_empty_game()
            database[admin_key]["game"]["is_registration_open"] = True
            
            link = f"https://t.me/{(await context.bot.get_me()).username}?start=reg{c_id}"
            await safe_send(context, chat_id=c_id, text=f"✨ بوابات الساحة فُتحت! للتسجيل اضغط على الرابط:\n🔗 {link}")
            await update.message.reply_text("📣 تم الإعلان في القناة وفتح باب التسجيل بنجاح.")
            
            counter_msg = await safe_send(context, chat_id=c_id, text="👥 عدد المشتركين الحالي: 0")
            if counter_msg: database[admin_key]["game"]["counter_msg_id"] = counter_msg.message_id
            return
            
        elif "ابدأ الملحمة" in text:
            game = database[admin_key].get("game")
            if not game or not game.get("players"):
                await update.message.reply_text("⚠️ المعركة لم تبدأ بعد، أو لا يوجد لاعبين مسجلين."); return
            if game.get("is_game_started"):
                await update.message.reply_text("⚠️ المعركة جارية بالفعل! لا يمكنك بدءها مرة أخرى."); return
                
            if len(game["players"]) >= 2:
                game["is_registration_open"], game["is_game_started"] = False, True
                await safe_send(context, chat_id=database[admin_key]["channel_id"], text="⚔️ أُغلقت الأبواب.. وبدأت الملحمة رسمياً!")
                await update.message.reply_text("🎮 انطلقنا! القائمة جاهزة أدناه:")
                await send_admin_summary(admin_key, context)
            else: await update.message.reply_text("⚠️ نحتاج إلى لاعبين اثنين على الأقل لبدء المعركة."); return
            
        elif "إيقاف اللعبة" in text:
            game = database[admin_key].get("game")
            if not game or (not game.get("is_game_started") and not game.get("is_registration_open")):
                await update.message.reply_text("⚠️ اللعبة منتهية أو لا توجد معركة جارية حالياً لإيقافها."); return
            
            if game.get("turn_timer_task"):
                try: game["turn_timer_task"].cancel()
                except Exception: pass
                
            stop_msg = "🛑 تم إيقاف اللعبة الحالية من قبل الإدارة.\n\n💡 اطلب من الادمن بدء اللعبة من جديد او انتظر حتى ينتهي الدور والمشاركة فالرابط مرة اخرى."
            for p_id in list(game.get("players", {}).keys()) + list(game.get("waiting_for_name", [])):
                await safe_send(context, chat_id=p_id, text=stop_msg)
                if p_id in global_active_players: del global_active_players[p_id]
            
            database[admin_key]["game"] = None 
            c_id = database[admin_key].get("channel_id")
            if c_id: await safe_send(context, chat_id=c_id, text="🛑 تم إيقاف اللعبة وإلغاء التسجيل من قبل الإدارة.")
            await update.message.reply_text("✅ تم إيقاف اللعبة، وإشعار جميع المسجلين، وتصفية الساحة بنجاح.")
            return

        elif "تدوير الروليت" in text:
            game = database[admin_key].get("game")
            if not game or not game.get("is_game_started"): await update.message.reply_text("⚠️ لا توجد معركة جارية حالياً."); return
            if game.get("current_turn") is not None: await update.message.reply_text("⏳ مهلاً! هناك دور جارٍ بالفعل للاعب آخر."); return
            max_el = len(game["players"]) - 1
            kb = [[InlineKeyboardButton(str(i), callback_data=f"set_{admin_key}_{i}") for i in range(1, min(max_el, 5) + 1)]]
            await update.message.reply_text(f"🎯 حدد عدد الضحايا (الاستبعادات) لهذه الجولة:", reply_markup=InlineKeyboardMarkup(kb)); return
        elif "ربط القناة" in text:
            await update.message.reply_text("📡 قم بتوجيه (Forward) أي رسالة من قناتك إلى هنا لربطها فوراً."); return

    # --- الإضافة الجماعية للمشرفين ---
    if is_owner and context.user_data.get("action") == "add_admin":
        usernames = re.split(r'[\s,\n]+', text.strip()) 
        added_list = []
        for u in usernames:
            if not u: continue
            target = u.lower()
            if not target.startswith("@"): target = "@" + target
            database[target] = {"username": target, "channel_id": None, "game": None}
            added_list.append(target)
            
        if added_list:
            await update.message.reply_text("✅ تمت ترقية المشرفين بنجاح:\n" + "\n".join(added_list))
        else:
            await update.message.reply_text("⚠️ لم يتم العثور على يوزرات صحيحة في رسالتك.")
        context.user_data["action"] = None; return
        
    elif is_owner and context.user_data.get("action") == "rem_admin":
        target = text.strip().lower()
        if not target.startswith("@"): target = "@" + target
        keys = [k for k, v in database.items() if v.get("username") == target]
        for k in keys: del database[k]
        await update.message.reply_text(f"✅ تم تجريد {target} من صلاحياته."); context.user_data["action"] = None; return

    if update.message.forward_origin and is_admin:
        origin = update.message.forward_origin
        c_chat = getattr(origin, 'chat', getattr(origin, 'sender_chat', None))
        if hasattr(c_chat, 'id'):
            c_id = c_chat.id
            database[admin_key]["channel_id"], channel_to_admin[c_id] = c_id, admin_key
            await update.message.reply_text(f"📡 تم الربط بنجاح مع القناة ذات المعرف: {c_id}")

    if not is_admin and update.message.chat.type == 'private':
        if user_id in global_active_players: 
            await update.message.reply_text("⚔️ أنت تخوض معركة جارية بالفعل! ركز في لعبتك.")
        else: 
            await update.message.reply_text("مرحباً بك! يرجى الدخول إلى اللعبة من خلال الرابط الرسمي المخصص الموجود في القناة.")

# --- 6. معالجة الأزرار الشفافة ---
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    if query.data == "none": return
    
    try:
        parts = query.data.split("_"); action = parts[0]; admin_id = int(parts[1])
    except ValueError:
        await query.answer("⚠️ يرجى تحديث البوت وإرسال رسالة له أولاً.", show_alert=True); return

    game = database.get(admin_id, {}).get("game")
    if not game: return

    if action == "set":
        num = int(parts[2]); game["required_eliminations"] = num
        try: await query.delete_message()
        except: pass
        await start_turn(admin_id, context)

    elif action == "out" and update.effective_user.id == game.get("current_turn"):
        tid = int(parts[2])
        if tid in game.get("players", {}):
            name = game["players"].pop(tid)["name"]; game["current_eliminations"] += 1
            if tid in global_active_players: del global_active_players[tid]
            
            out_msg = "💀 لقد تم إقصاؤك من الساحة!\n\n💡 اطلب من الادمن بدء اللعبة من جديد او انتظر حتى ينتهي الدور والمشاركة فالرابط مرة اخرى."
            await safe_send(context, chat_id=tid, text=out_msg)
            await safe_send(context, chat_id=database[admin_id]["channel_id"], text=f"💀 ضربة قاضية! سقط اللاعب: {name}")
            
            await query.answer("✅ تمت المهمة بنجاح!", show_alert=False)
            
            is_winner = await check_winner(admin_id, context)
            if not is_winner:
                if game["current_eliminations"] < game["required_eliminations"]:
                    await send_player_menu(admin_id, update.effective_user.id, context) 
                else: 
                    game["current_turn"] = None
                    try: await query.edit_message_text("✅ تمت المهمة بنجاح! لقد استبعدت العدد المطلوب.")
                    except: pass
                    await send_admin_summary(admin_id, context)
            else:
                try: await query.edit_message_text("✅ تمت المهمة بنجاح! لقد قضيت على آخر خصم.")
                except: pass

    elif action == "rev" and update.effective_user.id == game.get("current_turn"):
        if game["players"][update.effective_user.id].get("has_reveal"):
            game["players"][update.effective_user.id]["has_reveal"] = False 
            kbd = [[InlineKeyboardButton(f"🕵️‍♂️ كشف: {d['name']}", callback_data=f"dorev_{admin_id}_{p}")] for p, d in game["players"].items() if p != update.effective_user.id]
            try: await query.edit_message_text("اختر ضحيتك لفضحه أمام الجميع:", reply_markup=InlineKeyboardMarkup(kbd))
            except: pass
        else: 
            try: await query.edit_message_text("🚫 لقد استنفدت محاولة الكشف الخاصة بك!")
            except: pass

    elif action == "dorev":
        vid = int(parts[2]); info = game["players"][vid]; seeker = game["players"][update.effective_user.id]['name']
        reveal_txt = f"🕵️‍♂️ فضيحة مدوية! صاحب الدور [{seeker}] قام بكشف القناع عن:\n👤 الاسم المستعار: {info['name']}\n🔗 الحساب: {info['user']}"
        await safe_send(context, chat_id=database[admin_id]["channel_id"], text=reveal_txt)
        
        try: await query.edit_message_text(f"✅ تمت الفضيحة بنجاح!\n{reveal_txt}")
        except: pass
        await send_player_menu(admin_id, update.effective_user.id, context)

    elif action == "kickmenu":
        kbd = [[InlineKeyboardButton(f"❌ طرد: {d['name']}", callback_data=f"kick_{admin_id}_{p}")] for p, d in game.get("players", {}).items()]
        try: await query.edit_message_text("اختر اللاعب المراد طرده من الساحة:", reply_markup=InlineKeyboardMarkup(kbd))
        except: pass
        
    elif action == "kick":
        tid = int(parts[2])
        if tid in game.get("players", {}):
            game["players"].pop(tid)
            if tid in global_active_players: del global_active_players[tid]
            
            kick_msg = "🛑 تم إقصاؤك من الساحة بقرار إداري!\n\n💡 اطلب من الادمن بدء اللعبة من جديد او انتظر حتى ينتهي الدور والمشاركة فالرابط مرة اخرى."
            await safe_send(context, chat_id=tid, text=kick_msg)
            
            try: await query.edit_message_text("✅ تم تنفيذ الطرد الإداري بنجاح.")
            except: pass
            await safe_send(context, chat_id=database[admin_id]["channel_id"], text="📜 قرار إداري صارم: تم إقصاء لاعب من الساحة لتجاوز القوانين!")
            
            is_winner = await check_winner(admin_id, context)
            if not is_winner: await send_admin_summary(admin_id, context)
            
    elif action == "refresh":
        try: await query.delete_message()
        except: pass
        await send_admin_summary(admin_id, context)

# --- 7. نقطة الدخول القوية ---
async def start_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    uid = u.effective_user.id; u_name = u.effective_user.username
    current_username = f"@{u_name.lower()}" if u_name else None
    
    if c.args and c.args[0].startswith("reg"):
        # التعديل الإضافي: حماية ضد ثغرة "الرابط القاتل" حتى لا يتوقف البوت أبداً
        try:
            channel_id_part = int(c.args[0][3:])
            aid = channel_to_admin.get(channel_id_part)
        except (ValueError, TypeError):
            await u.message.reply_text("⚠️ عذراً، هذا الرابط غير صالح.")
            return

        if aid and database[aid].get("game") and database[aid]["game"].get("is_registration_open"):
            if uid in database[aid]["game"]["players"] or uid in database[aid]["game"]["waiting_for_name"]:
                await u.message.reply_text("⚠️ مهلاً! لا يمكنك دخول نفس اللعبة مرتين."); return
            if uid in global_active_players:
                await u.message.reply_text("⚠️ أنت مسجل بالفعل في معركة أخرى جارية!"); return
            
            database[aid]["game"]["waiting_for_name"].add(uid)
            await u.message.reply_text("مرحباً بك أيها المحارب! أرسل اسمك المستعار الآن لدخول الساحة (أقصى حد 40 حرف):")
            return
            
    is_admin = (uid == OWNER_ID) or any(str(v.get("username", "")).lower() == current_username for v in database.values())
    if is_admin:
        kb = get_owner_keyboard() if uid == OWNER_ID else get_admin_keyboard()
        await u.message.reply_text("🎬 أهلاً بالقيادة.. البوت جاهز لتعليماتك.", reply_markup=kb)

if __name__ == '__main__':
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_messages))
    app.run_polling()
