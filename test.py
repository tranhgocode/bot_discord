import discord
from discord.ext import commands, tasks
from datetime import datetime, timezone, timedelta
import re
import asyncio

# ============================================================
#   CẤU HÌNH — ĐIỀN THÔNG TIN CỦA BẠN VÀO ĐÂY
# ============================================================
TOKEN             = 'MTQ3OTU1MDQzMTM5ODA2ODMxNw.GXHrLg.ImYwhVR6yF6G4Msl8cAY5N93znLEvx1CLhmeR0'
SOURCE_CHANNEL_ID = 1477117253794136247   # Kênh nguồn chứa Hourly Report
TARGET_CHANNEL_ID = 1479559971518812342   # Kênh đích để chuyển tiếp

SEARCH_WINDOW_MINUTES = 1   # Cửa sổ tìm kiếm ±N phút quanh giờ tròn
# ============================================================

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

# Pattern: [hh:mm:ss] Hourly Report  (không phân biệt hoa/thường)
HOURLY_PATTERN = re.compile(r'^\[\d{2}:\d{2}:\d{2}\]\s+Hourly\s+Report', re.IGNORECASE)

# Lưu ID tin nhắn đã forward để tránh gửi trùng
forwarded_ids: set[int] = set()

# ──────────────────────────────────────────────
#   HELPER
# ──────────────────────────────────────────────

def is_hourly_report(msg: discord.Message) -> bool:
    """Trả về True nếu tin nhắn là Hourly Report."""
    for embed in msg.embeds:
        if embed.title and HOURLY_PATTERN.match(embed.title):
            return True
        if embed.description and HOURLY_PATTERN.match(embed.description.split('\n')[0]):
            return True
    if msg.content and HOURLY_PATTERN.match(msg.content):
        return True
    return False

async def forward_message(msg: discord.Message, target: discord.TextChannel, label: str = "") -> bool:
    """Gửi tin nhắn sang kênh đích và đánh dấu đã gửi."""
    if msg.id in forwarded_ids:
        print(f"[SKIP] Tin nhắn {msg.id} đã được forward trước đó.")
        return False
    try:
        if msg.embeds:
            await target.send(embeds=msg.embeds)
        elif msg.content:
            await target.send(content=msg.content)
        forwarded_ids.add(msg.id)
        ts = fmt(to_vn(msg.created_at))
        print(f"[FORWARD{label}] ✅ ID={msg.id} | Tạo lúc {ts} VN → #{target.name}")
        return True
    except discord.Forbidden:
        print("[ERROR] Bot không có quyền gửi vào kênh đích.")
    except Exception as e:
        print(f"[ERROR] {e}")
    return False

VN_TZ = timezone(timedelta(hours=7))

def now_utc() -> datetime:
    return datetime.now(timezone.utc)

def now_vn() -> datetime:
    return datetime.now(VN_TZ)

def to_vn(dt: datetime) -> datetime:
    return dt.astimezone(VN_TZ)

def fmt(dt: datetime) -> str:
    return dt.strftime('%H:%M:%S')

async def find_latest_hourly_report(source: discord.TextChannel, limit: int = 200) -> discord.Message | None:
    """Tìm tin nhắn Hourly Report gần nhất trong lịch sử kênh (fallback)."""
    async for msg in source.history(limit=limit):
        if is_hourly_report(msg):
            return msg
    return None

# ──────────────────────────────────────────────
#   REAL-TIME: nhận tin ngay khi webhook gửi
# ──────────────────────────────────────────────

@bot.event
async def on_message(message: discord.Message):
    # Bỏ qua tin của chính bot
    if message.author == bot.user:
        await bot.process_commands(message)
        return

    # Chỉ xử lý tin từ kênh nguồn do webhook gửi
    if message.channel.id == SOURCE_CHANNEL_ID and message.webhook_id is not None:
        if is_hourly_report(message):
            now       = now_utc()
            last_hour = now.replace(minute=0, second=0, microsecond=0)
            w_start   = last_hour - timedelta(minutes=SEARCH_WINDOW_MINUTES)
            w_end     = last_hour + timedelta(minutes=SEARCH_WINDOW_MINUTES)

            # Chỉ auto-forward nếu tin nằm trong cửa sổ giờ tròn
            if w_start <= message.created_at <= w_end:
                target = bot.get_channel(TARGET_CHANNEL_ID)
                if target:
                    print(f"[REALTIME] Phát hiện Hourly Report lúc {fmt(to_vn(message.created_at))} VN")
                    await forward_message(message, target, label=" (realtime)")

    await bot.process_commands(message)

# ──────────────────────────────────────────────
#   SCHEDULED: tự động chạy đúng giờ tròn
#   (backup phòng khi realtime bị miss)
# ──────────────────────────────────────────────

@tasks.loop(minutes=1)
async def hourly_check_loop():
    now = now_utc()
    if now.minute != 0:
        return  # Chỉ chạy tại phút 0

    source = bot.get_channel(SOURCE_CHANNEL_ID)
    target = bot.get_channel(TARGET_CHANNEL_ID)
    if not source or not target:
        print("[WARN] Không tìm thấy kênh nguồn hoặc kênh đích.")
        return

    last_hour = now.replace(minute=0, second=0, microsecond=0)
    w_start   = last_hour - timedelta(minutes=SEARCH_WINDOW_MINUTES)
    w_end     = last_hour + timedelta(minutes=SEARCH_WINDOW_MINUTES)

    print(f"[HOURLY] ⏰ {fmt(to_vn(now))} VN — Tìm trong [{fmt(to_vn(w_start))} – {fmt(to_vn(w_end))}]")

    candidates = []
    async for msg in source.history(after=w_start, before=w_end, oldest_first=False):
        if is_hourly_report(msg):
            candidates.append(msg)

    if not candidates:
        print(f"[HOURLY] ❌ Không tìm thấy trong cửa sổ. Tìm báo cáo gần nhất...")
        best = await find_latest_hourly_report(source)
        if best:
            print(f"[HOURLY] 🔄 Fallback: Dùng báo cáo lúc {fmt(to_vn(best.created_at))} VN")
            await forward_message(best, target, label=" (scheduled-fallback)")
        else:
            print(f"[HOURLY] ❌ Không tìm thấy Hourly Report nào trong lịch sử.")
        return

    best = min(candidates, key=lambda m: abs((m.created_at - last_hour).total_seconds()))
    await forward_message(best, target, label=" (scheduled)")

@hourly_check_loop.before_loop
async def before_hourly_loop():
    await bot.wait_until_ready()
    now  = now_utc()
    wait = 60 - now.second - now.microsecond / 1_000_000
    print(f"[INIT] Đồng bộ vòng lặp — chờ {wait:.1f}s đến đầu phút tiếp theo...")
    await asyncio.sleep(wait)

# ──────────────────────────────────────────────
#   LỆNH: !help
# ──────────────────────────────────────────────

@bot.command(name='help')
async def help_cmd(ctx: commands.Context):
    embed = discord.Embed(
        title="📋 Danh sách lệnh",
        color=discord.Color.blurple(),
        timestamp=now_utc()
    )
    embed.add_field(
        name="!help",
        value="Hiển thị danh sách tất cả các lệnh.",
        inline=False
    )
    embed.add_field(
        name="!check",
        value=(
            f"Tìm tin nhắn **Hourly Report gần nhất** (trong ±{SEARCH_WINDOW_MINUTES} phút quanh giờ tròn) "
            "và chuyển tiếp sang kênh đích. Không forward nếu đã gửi rồi."
        ),
        inline=False
    )
    embed.add_field(
        name="!status",
        value="Hiển thị trạng thái bot: kênh theo dõi, cửa sổ tìm kiếm, số tin đã forward, thời gian đến giờ tròn tiếp theo.",
        inline=False
    )
    embed.set_footer(text="Bot tự động chuyển tiếp Hourly Report mỗi giờ tròn (real-time + scheduled).")
    await ctx.send(embed=embed)

# ──────────────────────────────────────────────
#   LỆNH: !check
# ──────────────────────────────────────────────

@bot.command(name='check')
async def check_cmd(ctx: commands.Context):
    source = bot.get_channel(SOURCE_CHANNEL_ID)
    target = bot.get_channel(TARGET_CHANNEL_ID)

    if not source or not target:
        await ctx.send("⚠️ Không tìm thấy kênh nguồn hoặc kênh đích. Kiểm tra lại ID.")
        return

    now       = now_utc()
    last_hour = now.replace(minute=0, second=0, microsecond=0)
    w_start   = last_hour - timedelta(minutes=SEARCH_WINDOW_MINUTES)
    w_end     = last_hour + timedelta(minutes=SEARCH_WINDOW_MINUTES)

    status_msg = await ctx.send(
        f"🔍 Đang tìm Hourly Report trong `{fmt(to_vn(w_start))} – {fmt(to_vn(w_end))} VN`..."
    )

    candidates = []
    async for msg in source.history(after=w_start, before=w_end, oldest_first=False):
        if is_hourly_report(msg):
            candidates.append(msg)

    if not candidates:
        await status_msg.edit(content="⚠️ Không có báo cáo trong cửa sổ. Đang tìm báo cáo gần nhất...")
        best = await find_latest_hourly_report(source)
        if not best:
            await status_msg.edit(content="❌ Không tìm thấy Hourly Report nào trong lịch sử kênh.")
            return
        success = await forward_message(best, target, label=" (!check-fallback)")
        if success:
            await status_msg.edit(
                content=f"✅ Đã chuyển tiếp báo cáo gần nhất (tạo lúc `{fmt(to_vn(best.created_at))} VN`) sang <#{TARGET_CHANNEL_ID}>!"
            )
        else:
            await status_msg.edit(
                content=f"⚠️ Báo cáo gần nhất (ID: `{best.id}`) đã được forward trước đó rồi."
            )
        return

    best    = min(candidates, key=lambda m: abs((m.created_at - last_hour).total_seconds()))
    success = await forward_message(best, target, label=" (!check)")

    if success:
        await status_msg.edit(
            content=f"✅ Đã chuyển tiếp Hourly Report (tạo lúc `{fmt(to_vn(best.created_at))} VN`) sang <#{TARGET_CHANNEL_ID}>!"
        )
    else:
        await status_msg.edit(
            content=f"⚠️ Tin nhắn này đã được forward trước đó rồi (ID: `{best.id}`)."
        )

# ──────────────────────────────────────────────
#   LỆNH: !status
# ──────────────────────────────────────────────

@bot.command(name='status')
async def status_cmd(ctx: commands.Context):
    now       = now_utc()
    last_hour = now.replace(minute=0, second=0, microsecond=0)
    next_hour = last_hour + timedelta(hours=1)
    remaining = next_hour - now
    mins, secs = divmod(int(remaining.total_seconds()), 60)

    embed = discord.Embed(
        title="🤖 Trạng thái Bot",
        color=discord.Color.green(),
        timestamp=now
    )
    embed.add_field(name="🕐 Giờ hiện tại (VN)",        value=f"`{fmt(to_vn(now))}`",         inline=True)
    embed.add_field(name="⏳ Giờ tròn tiếp theo",       value=f"`{fmt(to_vn(next_hour))}`", inline=True)
    embed.add_field(name="⌛ Còn lại",                  value=f"`{mins:02d}:{secs:02d}`",   inline=True)
    embed.add_field(name="📡 Kênh nguồn",               value=f"<#{SOURCE_CHANNEL_ID}>",    inline=True)
    embed.add_field(name="📨 Kênh đích",                value=f"<#{TARGET_CHANNEL_ID}>",    inline=True)
    embed.add_field(name="🔍 Cửa sổ tìm kiếm",         value=f"`±{SEARCH_WINDOW_MINUTES} phút`", inline=True)
    embed.add_field(name="📬 Đã forward (session)",     value=f"`{len(forwarded_ids)}`",    inline=True)
    embed.add_field(name="🔄 Vòng lặp scheduled",
                    value="✅ Đang chạy" if hourly_check_loop.is_running() else "❌ Dừng",
                    inline=True)
    embed.add_field(name="📶 Real-time listener",       value="✅ Đang chạy",               inline=True)
    await ctx.send(embed=embed)

# ──────────────────────────────────────────────
#   KHỞI ĐỘNG
# ──────────────────────────────────────────────

@bot.event
async def on_ready():
    print("=" * 50)
    print(f"✅  Bot sẵn sàng  |  {bot.user}")
    print(f"📡  Kênh nguồn   : {SOURCE_CHANNEL_ID}")
    print(f"📨  Kênh đích    : {TARGET_CHANNEL_ID}")
    print(f"⏱️   Cửa sổ       : ±{SEARCH_WINDOW_MINUTES} phút")
    print("=" * 50)
    if not hourly_check_loop.is_running():
        hourly_check_loop.start()

bot.run(TOKEN)