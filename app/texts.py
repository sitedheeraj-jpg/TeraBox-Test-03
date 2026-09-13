from __future__ import annotations

from app.settings import settings

DEFAULT_WELCOME = (
    "<b>ᴛᴇʀᴀᴅʀᴏᴘ</b>\n"
    "<blockquote>fast, simple file delivery from your public share link.</blockquote>\n\n"
    "send a <b>ᴛᴇʀᴀʙᴏx</b> link here and i’ll prepare the file.\n\n"
    "• stream instantly\n"
    "• choose hd when available\n"
    "• download into this chat with live progress\n\n"
    "<i>paste a link whenever you’re ready.</i>"
)

HELP = (
    "<b>ᴜsɪɴɢ ᴛᴇʀᴀᴅʀᴏᴘ</b>\n\n"
    "<blockquote>1. copy a public share link\n"
    "2. paste it here\n"
    "3. pick stream, hd, direct, or send file</blockquote>\n\n"
    "downloads show a live progress bar, speed, elapsed time, and eta.\n"
    "larger files stay available through stream and direct links unless a local "
    "telegram bot api server is enabled.\n\n"
    "owner controls: /owner"
)

OWNER_HELP = (
    "<b>ᴏᴡɴᴇʀ ᴄᴏɴᴛʀᴏʟs</b>\n\n"
    "use the buttons below for the dashboard.\n\n"
    "<b>commands</b>\n"
    "/stats — usage overview\n"
    "/broadcast &lt;text&gt; — message users\n"
    "/ban &lt;user_id&gt; · /unban &lt;user_id&gt;\n"
    "/auth &lt;user_id&gt; · /unauth &lt;user_id&gt;\n"
    "/maintenance on|off\n"
    "/setwelcome — reply to a message\n"
    "/setcaption &lt;template&gt;\n"
    "/setlimit &lt;mb&gt; · /users · /logs"
)

DETECTING = "<b>ᴄʜᴇᴄᴋɪɴɢ ʟɪɴᴋ</b>\n<blockquote>i found your share link. checking it now…</blockquote>"
ANALYSING = "<b>ᴀɴᴀʟʏsɪɴɢ sʜᴀʀᴇ</b>\n<blockquote>domain: {domain}\nshare id: {surl}</blockquote>"
RETRIEVING = "<b>ғᴇᴛᴄʜɪɴɢ ᴅᴇᴛᴀɪʟs</b>\n<blockquote>this usually takes a few seconds.</blockquote>"
RESOLVING = "<b>ᴘʀᴇᴘᴀʀɪɴɢ ᴏᴘᴛɪᴏɴs</b>\n<blockquote>matching the best available file links.</blockquote>"
READY = "<b>ғɪʟᴇ ʀᴇᴀᴅʏ</b>\n<blockquote>{filename}\nsize: {size}\ntype: {kind}</blockquote>\nchoose an option below."
FOLDER = "<b>ғᴏʟᴅᴇʀ ʀᴇᴀᴅʏ</b>\n<blockquote>{title}\n{count} files · {size}</blockquote>\npick a file below."
FINISHED = "<b>ᴅᴏɴᴇ</b>\n<blockquote>{filename}\n{size} · {speed} average\ncompleted in {duration}</blockquote>"
TOO_LARGE = (
    "<b>ғɪʟᴇ ʟɪᴍɪᴛ ʀᴇᴀᴄʜᴇᴅ</b>\n"
    "<blockquote>this file is {size}.\n"
    "the current bot upload limit is {limit} mb.</blockquote>\n\n"
    "{hint}"
)
LARGE_UPLOAD_UNAVAILABLE = (
    "<b>ʟᴀʀɢᴇ ғɪʟᴇ sᴇɴᴅ ɪs ɴᴏᴛ ᴇɴᴀʙʟᴇᴅ</b>\n"
    "<blockquote>this deployment is using telegram’s hosted bot api, "
    "which limits bot uploads to 49 mb.</blockquote>\n\n"
    "stream and direct are still available. to send files above 49 mb, "
    "connect a reachable local bot api server with <code>BOT_API_URL</code>."
)
NO_LINK = "<b>ɴᴏ sʜᴀʀᴇ ʟɪɴᴋ ғᴏᴜɴᴅ</b>\n\nsend a public terabox link and i’ll take it from there."
PRIVATE = "<b>ᴘʀɪᴠᴀᴛᴇ ʙᴏᴛ</b>\nask the owner to authorise your account."
BANNED = "<b>ᴀᴄᴄᴇss ʀᴇᴠᴏᴋᴇᴅ</b>\nthis account can’t use the bot."
MAINTENANCE = "<b>ᴛᴇᴍᴘᴏʀᴀʀʏ ᴘᴀᴜsᴇ</b>\nplease try again in a little while."
FAILED = "<b>ᴛʜᴀᴛ ᴅɪᴅɴ’ᴛ ᴡᴏʀᴋ</b>\n<blockquote>{reason}</blockquote>\ncheck that the link is public, then try again."
NOT_OWNER = "<b>ᴏᴡɴᴇʀ ᴏɴʟʏ</b>\nthat command is only available to the bot owner."

ADMIN_PANEL = (
    "<b>ᴛᴇʀᴀᴅʀᴏᴘ ᴀᴅᴍɪɴ</b>\n"
    "<blockquote>users: {users} · downloads: {downloads} · bans: {bans}\n"
    "maintenance: {maintenance}\n"
    "upload ceiling: {limit} mb</blockquote>\n"
    "choose an area below."
)


def welcome() -> str:
    return settings.welcome_text.strip() or DEFAULT_WELCOME
