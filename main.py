import os
import logging
import sqlite3
import asyncio
import nest_asyncio
import traceback
from collections import defaultdict
from typing import DefaultDict, Dict, List, Any, Optional
import re
from dotenv import load_dotenv
from about_user_ai import generate_summary
from telegram import Update, LabeledPrice, InlineKeyboardButton, InlineKeyboardMarkup, Message
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters,
    CallbackContext, ContextTypes, ConversationHandler,
    CallbackQueryHandler, PreCheckoutQueryHandler,
)

ITEMS: Dict[str, Dict[str, Any]] = {
    'about_user_dict': {
        'name': 'about_user_dict',
        'price': 1,
        'description': 'about_user funtion payment',
    },
    'vip': {
        'name': 'vip',
        'price': 50,
        'description': '1 month subscription for all features',
    },
    'find_team': {
        'name': 'teamfinder_function',
        'price': 3,
        'description': 'payment for finding a team',
    }
}

MESSAGES = {
    'refund_success': (
        "✅ Refund processed successfully!\n"
        "The Stars have been returned to your balance."
    ),
    'refund_failed': (
        "❌ Refund could not be processed.\n"
        "Please try again later or contact support."
    ),
    'refund_usage': (
        "Please provide the transaction ID after the /refund command.\n"
        "Example: `/refund YOUR_TRANSACTION_ID`"
    )
}

# Apply async patch
nest_asyncio.apply()

# Load environment variables
load_dotenv()
API_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
your_provider_token = os.getenv("TEST_TOKEN")

# Set up logging
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# Store statistics
STATS: Dict[str, DefaultDict[str, int]] = {
    'purchases': defaultdict(int),
    'refunds': defaultdict(int)
}
# Database file
DB_FILE = "teamfinder.db"

# States
PHONE, EMAIL, SELECTING_SKILLS, WAITING_FOR_PORTFOLIO, WAITING_FOR_EDIT, WAITING_FOR_PREFERENCES, TEAM_FINDING = range(7)

async def refund_command(update: Update, context: CallbackContext) -> None:
    """Handle /refund command - process refund requests."""
    if not context.args:
        await update.message.reply_text(
            MESSAGES['refund_usage']
        )
        return

    try:
        charge_id = context.args[0]
        user_id = update.effective_user.id

        # Call the refund API, adjust for the Stars payment system
        success = await context.bot.refund_star_payment(
            user_id=user_id,
            telegram_payment_charge_id=charge_id
        )

        if success:
            STATS['refunds'][str(user_id)] += 1
            await update.message.reply_text(MESSAGES['refund_success'])
        else:
            await update.message.reply_text(MESSAGES['refund_failed'])

    except Exception as e:
        error_text = f"Error type: {type(e).__name__}\n"
        error_text += f"Error message: {str(e)}\n"
        error_text += f"Traceback:\n{''.join(traceback.format_tb(e.__traceback__))}"
        logger.error(error_text)

        await update.message.reply_text(
            f"❌ Sorry, there was an error processing your refund:\n"
            f"Error: {type(e).__name__} - {str(e)}\n\n"
            "Please make sure you provided the correct transaction ID and try again."
        )


async def button_handler(update: Update, context: CallbackContext) -> None:
    """Handle button clicks for item selection."""
    query = update.callback_query
    if not query or not query.message:
        return

    try:
        await query.answer()

        item_id = query.data
        item = ITEMS[item_id]

        # Make sure message exists before trying to use it
        if not isinstance(query.message, Message):
            return

        # Make sure you have the correct provider token set if needed
        await context.bot.send_invoice(
            chat_id=query.message.chat_id,
            title=item['name'],
            description=item['description'],
            payload=item_id,
            provider_token= your_provider_token,  # This should be your valid token if using an external payment system
            currency="XTR",  # Telegram Stars
            prices=[LabeledPrice(item['name'], int(item['price']))],
            start_parameter="start_parameter"
        )

    except Exception as e:
        logger.error(f"Error in button_handler: {str(e)}")
        if query and query.message and isinstance(query.message, Message):
            await query.message.reply_text(
                "Sorry, something went wrong while processing your request."
            )


async def precheckout_callback(update: Update, context: CallbackContext) -> None:
    """Handle pre-checkout queries."""
    query = update.pre_checkout_query
    if query.invoice_payload in ITEMS:
        await query.answer(ok=True)
    else:
        await query.answer(ok=False, error_message="Something went wrong...")


async def successful_payment_callback(update: Update, context: CallbackContext) -> None:
    """Handle successful payments."""
    payment = update.message.successful_payment
    item_id = payment.invoice_payload
    item = ITEMS[item_id]
    user_id = update.effective_user.id

    # Update statistics
    STATS['purchases'][str(user_id)] += 1

    logger.info(
        f"Successful payment from user {user_id} "
        f"for item {item_id} (charge_id: {payment.telegram_payment_charge_id})"
    )
    
    # Trigger the appropriate function based on the purchased item
    if item_id == 'about_user_dict':
        # For non-VIP users who purchased the about_user function directly
        set_about_user(user_id)
        await update.message.reply_text(
            "Thank you for your purchase! 🎉\n\n"
            "Your AI-generated profile summary has been created. Use /profile to view it.",
            parse_mode='Markdown'
        )
    elif item_id == 'vip':
        # Set VIP status for 1 month
        await set_vip_status(user_id)
        await update.message.reply_text(
            "Thank you for your purchase! 🎉\n\n"
            "You now have VIP status for 1 month with access to all premium features!\n"
            "You can use /about_me and /find_team commands for free during your subscription period.",
            parse_mode='Markdown'
        )
    elif item_id == 'find_team':
        # For non-VIP users who purchased the find_team function directly
        await update.message.reply_text(
            "Thank you for your purchase! 🎉\n\n"
            "Let's find you a team! Please tell me what kind of team you're looking for.",
            parse_mode='Markdown'
        )
        return TEAM_FINDING
    else:
        await update.message.reply_text(
            f"Thank you for your purchase! 🎉\n\n",
            parse_mode='Markdown'
        )


async def error_handler(update: Update, context: CallbackContext) -> None:
    """Handle errors caused by Updates."""
    logger.error(f"Update {update} caused error {context.error}")


# Connect to DB

def connect_db():

    try:

        return sqlite3.connect(DB_FILE, check_same_thread=False)

    except sqlite3.Error as e:

        logging.error(f"DB Error: {e}")

        return None


# Update 'about_user' field using AI

def set_about_user(user_id):

    conn = connect_db()

    cursor = conn.cursor()

    cursor.execute("SELECT name, email, username, phone_number, skills, preferences, portfolio FROM users WHERE id = ?", (user_id,))

    user = cursor.fetchone()

    if user:

        about_user_summary = generate_summary(user)

        cursor.execute("UPDATE users SET about_user = ? WHERE id = ?", (about_user_summary, user_id))

        conn.commit()

    conn.close()


# Update portfolio

def update_portfolio(user_id, portfolio_text):

    conn = connect_db()

    cursor = conn.cursor()

    cursor.execute("UPDATE users SET portfolio = ? WHERE id = ?", (portfolio_text, user_id))

    conn.commit()

    conn.close()


# Get portfolio

def get_portfolio(user_id):

    conn = connect_db()

    cursor = conn.cursor()

    cursor.execute("SELECT portfolio FROM users WHERE id = ?", (user_id,))

    row = cursor.fetchone()

    conn.close()

    return row[0] if row else None



# Update 'about_user' field using AI
def set_about_user(user_id):
    conn = connect_db()
    cursor = conn.cursor()
    cursor.execute("SELECT name, email, username, phone_number, skills, preferences, portfolio FROM users WHERE id = ?", (user_id,))
    user = cursor.fetchone()
    if user:
        about_user_summary = generate_summary(user)
        cursor.execute("UPDATE users SET about_user = ? WHERE id = ?", (about_user_summary, user_id))
        conn.commit()
    conn.close()

# Set VIP status
async def set_vip_status(user_id):
    from datetime import datetime, timedelta
    vip_until = (datetime.now() + timedelta(days=30)).isoformat()
    
    conn = connect_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET vip_until = ? WHERE id = ?", (vip_until, user_id))
    conn.commit()
    conn.close()

# Check if user is VIP
def is_vip(user_id):
    from datetime import datetime
    
    conn = connect_db()
    cursor = conn.cursor()
    cursor.execute("SELECT vip_until FROM users WHERE id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    
    if not result or not result[0]:
        return False
    
    vip_until = datetime.fromisoformat(result[0])
    return datetime.now() < vip_until

# Update portfolio
def update_portfolio(user_id, portfolio_text):
    conn = connect_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET portfolio = ? WHERE id = ?", (portfolio_text, user_id))
    conn.commit()
    conn.close()

# Get portfolio
def get_portfolio(user_id):
    conn = connect_db()
    cursor = conn.cursor()
    cursor.execute("SELECT portfolio FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None

# Get user profile
def get_user_profile(user_id):
    conn = connect_db()
    cursor = conn.cursor()
    cursor.execute("SELECT name, username, phone_number, email, skills, preferences, about_user, vip_until FROM users WHERE id = ?", (user_id,))
    user = cursor.fetchone()
    conn.close()
    
    if not user:
        return None
    
    name, username, phone, email, skills, preferences, about_user, vip_until = user
    
    profile = f"👤 *{name}* (@{username})\n\n"
    
    if phone:
        profile += f"📱 Phone: {phone}\n"
    if email:
        profile += f"✉️ Email: {email}\n"
    if skills:
        profile += f"\n🔧 *Skills*:\n{skills}\n"
    if preferences:
        profile += f"\n🌟 *Preferences*:\n{preferences}\n"
    if about_user:
        profile += f"\n📝 *About*:\n{about_user}\n"
    
    if vip_until:
        from datetime import datetime
        vip_date = datetime.fromisoformat(vip_until)
        if datetime.now() < vip_date:
            profile += f"\n👑 *VIP until*: {vip_date.strftime('%Y-%m-%d')}\n"
    
    return profile

# Find team members based on skills
def find_team_members(user_id, requirements):
    conn = connect_db()
    cursor = conn.cursor()
    
    # Get user's own skills
    cursor.execute("SELECT skills FROM users WHERE id = ?", (user_id,))
    user_skills_row = cursor.fetchone()
    if not user_skills_row or not user_skills_row[0]:
        conn.close()
        return "You need to set your skills first using /set_skills"
    
    # Parse requirements to find needed skills
    skills_needed = []
    for word in requirements.lower().split():
        if re.match(r'^[a-z0-9\+\#\.]+$', word) and len(word) > 2:
            skills_needed.append(word)
    
    if not skills_needed:
        conn.close()
        return "Please specify some skills you're looking for in your team"
    
    # Find users with matching skills
    matches = []
    cursor.execute("SELECT id, name, username, skills FROM users WHERE id != ?", (user_id,))
    for row in cursor.fetchall():
        other_id, other_name, other_username, other_skills = row
        if not other_skills:
            continue
        
        other_skills_list = [s.strip().lower() for s in other_skills.split(',')]
        match_score = sum(1 for skill in skills_needed if any(skill in other_skill for other_skill in other_skills_list))
        
        if match_score > 0:
            matches.append({
                'id': other_id,
                'name': other_name,
                'username': other_username,
                'skills': other_skills,
                'score': match_score
            })
    
    conn.close()
    
    # Sort matches by score
    matches.sort(key=lambda x: x['score'], reverse=True)
    
    # Format response
    if not matches:
        return "No team members found with the required skills. Try different requirements."
    
    result = f"🔍 Found {len(matches)} potential team members:\n\n"
    for i, match in enumerate(matches[:5], 1):
        result += f"{i}. {match['name']}"
        if match['username']:
            result += f" (@{match['username']})"
        result += f"\n   Skills: {match['skills']}\n\n"
    
    if len(matches) > 5:
        result += f"...and {len(matches) - 5} more matches."
    
    return result

# --- BOT HANDLERS ---

# /start
async def start(update: Update, context: CallbackContext):
    """Handle /start command."""
    response = """Hello\! Welcome to Team Finder Bot\! 🎯

Here's what I can do for you:
\- /help \- Get information about how to use the bot
Team Finder Bot 🤖
This bot helps you *build your professional portfolio*, *connect with teammates*, and *showcase your skills*\.
You can:

✅ *Set your skills & preferences* to match with the right people\.
✅ *Manage your portfolio* by adding completed projects\.
The more details you provide, the better the bot can *personalize recommendations* and help you find *ideal team members* for your projects\! 🚀"""

    await update.message.reply_text(response, parse_mode="MarkdownV2")


# /help
async def help_command(update: Update, context: CallbackContext):
    await update.message.reply_text(
        "📋 Commands:\n"
        "/start - Welcome message\n"
        "/sign_up - Register\n"
        "/portfolio - Show your portfolio\n"
        "/add_project - Add complited projects to your portfolio\n"
        "/set_skills - Add or update skills\n"
        "/set_preferences - Set work preferences\n"
        "/profile - View your profile\n"
        "/find_team - Find team members (free for VIP users)\n"
        "/about_me - Generate AI summary of your profile (free for VIP users)\n"
        "/shop - View available purchases\n"
        "/refund [transaction_id] - Request a refund"
    )

# /shop command
async def shop_command(update: Update, context: CallbackContext):
    keyboard = []
    for item_id, item in ITEMS.items():
        keyboard.append([InlineKeyboardButton(
            f"{item['name']} - {item['price']} Stars",
            callback_data=item_id
        )])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "🛒 Welcome to the Shop!\n"
        "Select an item to purchase:\n\n"
        "👑 VIP Subscription (50 Stars):\n"
        "- Access to /about_me and /find_team for free for 1 month\n\n"
        "Individual purchases:\n"
        "- Generate profile summary (1 Stars)\n"
        "- Find team members (3 Stars)",
        reply_markup=reply_markup
    )

# /profile command
async def profile_command(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    profile = get_user_profile(user_id)
    
    if profile:
        await update.message.reply_text(profile, parse_mode="Markdown")
    else:
        await update.message.reply_text(
            "You don't have a profile yet. Use /sign_up to create one."
        )

# /about_me command
async def about_me_command(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    
    # Check if user is VIP
    if is_vip(user_id):
        set_about_user(user_id)
        await update.message.reply_text(
            "Your AI-generated profile summary has been created. Use /profile to view it."
        )
    else:
        # Offer to purchase
        keyboard = [
            [InlineKeyboardButton(
                f"Purchase summary - {ITEMS['about_user_dict']['price']} Stars",
                callback_data="about_user_dict"
            )],
            [InlineKeyboardButton(
                f"Get VIP subscription - {ITEMS['vip']['price']} Stars",
                callback_data="vip"
            )]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "This feature requires payment or VIP subscription.\n\n"
            "You can either purchase this feature directly or get a VIP subscription "
            "which gives you access to all premium features for 1 month.",
            reply_markup=reply_markup
        )

# /find_team command
async def find_team_command(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    
    # Check if user is VIP
    if is_vip(user_id):
        await update.message.reply_text(
            "Please describe what kind of team members you're looking for. "
            "Include skills and any other requirements."
        )
        return TEAM_FINDING
    else:
        # Offer to purchase
        keyboard = [
            [InlineKeyboardButton(
                f"Purchase team finding - {ITEMS['find_team']['price']} Stars",
                callback_data="find_team"
            )],
            [InlineKeyboardButton(
                f"Get VIP subscription - {ITEMS['vip']['price']} Stars",
                callback_data="vip"
            )]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "This feature requires payment or VIP subscription.\n\n"
            "You can either purchase this feature directly or get a VIP subscription "
            "which gives you access to all premium features for 1 month.",
            reply_markup=reply_markup
        )
        return ConversationHandler.END

# Handle team finding requirements
async def handle_team_requirements(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    requirements = update.message.text
    
    result = find_team_members(user_id, requirements)
    await update.message.reply_text(result)
    return ConversationHandler.END

# Add project to portfolio

async def add_project(update: Update, context: CallbackContext):

    await update.message.reply_text(" Send me the project you've completed. Include name and short description.")

    return WAITING_FOR_PORTFOLIO


async def receive_project(update: Update, context: CallbackContext):

    user_id = update.message.from_user.id

    new_project = update.message.text


    conn = connect_db()

    cursor = conn.cursor()

    

    # Get current portfolio

    cursor.execute("SELECT portfolio FROM users WHERE id = ?", (user_id,))

    result = cursor.fetchone()

    current_portfolio = result[0] if result else ""

    

    # Append new project

    updated_portfolio = (current_portfolio + "\n\n " + new_project).strip()

    cursor.execute("UPDATE users SET portfolio = ? WHERE id = ?", (updated_portfolio, user_id))

    conn.commit()

    conn.close()


    await update.message.reply_text("? Project added to your portfolio!")

    return ConversationHandler.END


# /portfolio

async def portfolio(update: Update, context: CallbackContext):

    user_id = update.message.from_user.id

    portfolio_text = get_portfolio(user_id)

    if portfolio_text:

        await update.message.reply_text(f" Your Completed Projects:\n{portfolio_text}")

    else:

        await update.message.reply_text(" Your portfolio is empty.\nUse /add_project to list your first project.")


# /set_skills

async def ask_skills(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(" List your skills separated by commas (e.g., Python, React).")

    return SELECTING_SKILLS


async def handle_skills(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = update.message.from_user.id

    skills = update.message.text

    conn = connect_db()

    cursor = conn.cursor()

    cursor.execute("UPDATE users SET skills = ? WHERE id = ?", (skills, user_id))

    conn.commit()

    conn.close()

    await update.message.reply_text("? Skills updated.")

    return ConversationHandler.END


async def cancel_skills(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text("? Skill update cancelled.")

    return ConversationHandler.END


# /set_preferences

async def set_preferences(update: Update, context: CallbackContext):

    await update.message.reply_text(" Share your work preferences (e.g., remote only, startups, agile teams).")

    return WAITING_FOR_PREFERENCES


async def handle_preferences(update: Update, context: CallbackContext):

    user_id = update.message.from_user.id

    preferences = update.message.text

    conn = connect_db()

    if conn:

        try:

            cursor = conn.cursor()

            cursor.execute("UPDATE users SET preferences = ? WHERE id = ?", (preferences, user_id))

            conn.commit()

            await update.message.reply_text("? Preferences updated.")

        except sqlite3.Error as e:

            logging.error(f"DB error: {e}")

            await update.message.reply_text("? Failed to update preferences.")

        finally:

            conn.close()

    return ConversationHandler.END


# /sign_up

async def sign_up(update: Update, context: CallbackContext):

    user = update.message.from_user

    conn = connect_db()

    cursor = conn.cursor()

    cursor.execute("SELECT id FROM users WHERE id = ?", (user.id,))

    if cursor.fetchone():

        await update.message.reply_text("You're already registered. Use /modify to change your info.")

        return ConversationHandler.END

    cursor.execute("INSERT INTO users (id, name, username) VALUES (?, ?, ?)", (user.id, user.first_name, user.username))

    conn.commit()

    conn.close()

    await update.message.reply_text(" Enter your phone number:")

    return PHONE


# /modify

async def modify(update: Update, context: CallbackContext):

    await update.message.reply_text(" Enter your new phone number:")

    return PHONE


# /modify_email

async def modify_email(update: Update, context: CallbackContext):

    await update.message.reply_text(" Enter your new email:")

    return EMAIL


# Get phone number

async def get_phone(update: Update, context: CallbackContext):

    user_id = update.message.from_user.id

    phone = update.message.text

    if re.match(r"^\+?[1-9]\d{1,14}$", phone):

        conn = connect_db()

        cursor = conn.cursor()

        cursor.execute("UPDATE users SET phone_number = ? WHERE id = ?", (phone, user_id))

        conn.commit()

        conn.close()

        await update.message.reply_text(" Now enter your email:")

        return EMAIL

    await update.message.reply_text("? Invalid phone number. Try again.")

    return PHONE


# Get email

async def get_email(update: Update, context: CallbackContext):

    user_id = update.message.from_user.id

    email = update.message.text

    if re.match(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$", email):

        conn = connect_db()

        cursor = conn.cursor()

        cursor.execute("UPDATE users SET email = ? WHERE id = ?", (email, user_id))

        conn.commit()

        conn.close()

        await update.message.reply_text(" Registration complete!")

        return ConversationHandler.END

    await update.message.reply_text("? Invalid email format. Try again.")

    return EMAIL



# --- MAIN APP ---
async def main():
    application = Application.builder().token(API_TOKEN).build()

    # Register handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("portfolio", portfolio))
    application.add_handler(CommandHandler("shop", shop_command))
    application.add_handler(CommandHandler("profile", profile_command))
    application.add_handler(CommandHandler("about_me", about_me_command))
    application.add_handler(CommandHandler("refund", refund_command))

    # Portfolio project adder
    application.add_handler(ConversationHandler(
        entry_points=[CommandHandler("add_project", add_project)],
        states={WAITING_FOR_PORTFOLIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_project)]},
        fallbacks=[CommandHandler("cancel", lambda u, c: ConversationHandler.END)]
    ))

    # Team finder handler
    application.add_handler(ConversationHandler(
        entry_points=[CommandHandler("find_team", find_team_command)],
        states={TEAM_FINDING: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_team_requirements)]},
        fallbacks=[CommandHandler("cancel", lambda u, c: ConversationHandler.END)]
    ))

    # Sign up handler
    application.add_handler(ConversationHandler(
        entry_points=[CommandHandler("sign_up", sign_up)],
        states={
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone)],
            EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_email)]
        },
        fallbacks=[CommandHandler("cancel", lambda u, c: ConversationHandler.END)]
    ))

    # Modify handler
    application.add_handler(ConversationHandler(
        entry_points=[CommandHandler("modify", modify)],
        states={
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone)],
            EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_email)]
        },
        fallbacks=[CommandHandler("cancel", lambda u, c: ConversationHandler.END)]
    ))

    # Skills handler
    application.add_handler(ConversationHandler(
        entry_points=[CommandHandler("set_skills", ask_skills)],
        states={SELECTING_SKILLS: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_skills)]},
        fallbacks=[CommandHandler("cancel", cancel_skills)]
    ))

    # Preferences handler
    application.add_handler(ConversationHandler(
        entry_points=[CommandHandler("set_preferences", set_preferences)],
        states={WAITING_FOR_PREFERENCES: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_preferences)]},
        fallbacks=[CommandHandler("cancel", lambda u, c: ConversationHandler.END)]
    ))

    # Payment handlers
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_callback))

    # Error handler
    application.add_error_handler(error_handler)

    # Run the bot
    await application.run_polling()

if __name__ == '__main__':
    asyncio.run(main())