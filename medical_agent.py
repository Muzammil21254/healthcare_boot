import os
import bcrypt
from dotenv import load_dotenv
from PIL import Image as PILImage
from agno.agent import Agent
from agno.models.google import Gemini
import streamlit as st
from agno.tools.duckduckgo import DuckDuckGoTools
from agno.media import Image as AgnoImage
import datetime
import uuid
import pymongo
from pymongo import MongoClient
import io   
import tempfile 
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import timedelta
from voice_assistant import VoiceAssistant

# Load environment variables
load_dotenv()

# Connect to MongoDB
MONGO_URI = "mongodb+srv://muzammilit2002:i81ZypVBTakrQwL2@cluster0.eyndzcb.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"
client = MongoClient(MONGO_URI)
db = client["healthcare_assistant"]
users_collection = db["users"]
conversations_collection = db["conversations"]
medication_reminders_collection = db["medication_reminders"]

# Get API key from environment variables
api_key_from_env = os.getenv("GOOGLE_API_KEY")

# Email configuration for reminders
EMAIL_SENDER = os.getenv("EMAIL_SENDER", "your-email@gmail.com")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "your-app-password")
EMAIL_SMTP_SERVER = "smtp.gmail.com"
EMAIL_PORT = 587

# Initialize session states
if "GOOGLE_API_KEY" not in st.session_state:
    st.session_state.GOOGLE_API_KEY = api_key_from_env

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if "user_id" not in st.session_state:
    st.session_state.user_id = None

if "user_email" not in st.session_state:
    st.session_state.user_email = None

if "conversations" not in st.session_state:
    st.session_state.conversations = {}

if "current_conversation_id" not in st.session_state:
    st.session_state.current_conversation_id = None

if "page" not in st.session_state:
    st.session_state.page = "login"

if "is_recording" not in st.session_state:
    st.session_state.is_recording = False
if "voice_duration" not in st.session_state:
    st.session_state.voice_duration = 10
if "last_voice_text" not in st.session_state:
    st.session_state.last_voice_text = None

# Initialize medication page state
if "medication_edit_mode" not in st.session_state:
    st.session_state.medication_edit_mode = False

if "medication_edit_id" not in st.session_state:
    st.session_state.medication_edit_id = None

# Initialize session states for voice assistant
if "voice_assistant" not in st.session_state:
    st.session_state.voice_assistant = None

# Initialize the background scheduler for reminders
scheduler = BackgroundScheduler()
scheduler.start()

# Helper functions for authentication
def hash_password(password):
    """Hash a password for storing."""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode('utf-8'), salt)

def verify_password(stored_password, provided_password):
    """Verify a stored password against one provided by user"""
    return bcrypt.checkpw(provided_password.encode('utf-8'), stored_password)

def signup_user(email, password):
    """Create a new user in the database"""
    # Check if user already exists
    if users_collection.find_one({"email": email}):
        return False, "Email already registered"
    
    # Create new user
    user_id = str(uuid.uuid4())
    user_data = {
        "user_id": user_id,
        "email": email,
        "password": hash_password(password),
        "created_at": datetime.datetime.now()
    }
    
    users_collection.insert_one(user_data)
    return True, user_id

def login_user(email, password):
    """Authenticate a user"""
    user = users_collection.find_one({"email": email})
    if not user:
        return False, "Invalid email or password"
    
    if not verify_password(user["password"], password):
        return False, "Invalid email or password"
    
    return True, user["user_id"]

def logout_user():
    """Log out the current user"""
    st.session_state.authenticated = False
    st.session_state.user_id = None
    st.session_state.user_email = None
    st.session_state.conversations = {}
    st.session_state.current_conversation_id = None
    st.session_state.page = "login"
    st.rerun()

# Functions for conversation management
def load_user_conversations(user_id):
    """Load all conversations for a user from MongoDB"""
    conversations = {}
    for conv in conversations_collection.find({"user_id": user_id}):
        # Convert MongoDB _id to string for the dict key if needed
        conv_id = str(conv["conversation_id"])
        conversations[conv_id] = {
            "title": conv["title"],
            "timestamp": conv["timestamp"],
            "messages": conv["messages"],
            "context": conv["context"]
        }
    
    # If user has no conversations, create a new one
    if not conversations:
        return create_new_conversation_db(user_id)
    else:
        # Set current conversation to the most recent one
        sorted_convs = sorted(conversations.items(), key=lambda x: x[1]["timestamp"], reverse=True)
        current_id = sorted_convs[0][0]
        return conversations, current_id

def create_new_conversation_db(user_id):
    """Create a new conversation in the database"""
    conv_id = str(uuid.uuid4())
    current_time = datetime.datetime.now().strftime("%b %d, %Y %I:%M %p")
    
    new_conversation = {
        "conversation_id": conv_id,
        "user_id": user_id,
        "title": "New Conversation",
        "timestamp": current_time,
        "messages": [],
        "context": ""
    }
    
    conversations_collection.insert_one(new_conversation)
    
    # Also add to session state
    conversations = {
        conv_id: {
            "title": "New Conversation",
            "timestamp": current_time,
            "messages": [],
            "context": ""
        }
    }
    
    return conversations, conv_id

def update_conversation_db(user_id, conv_id, title=None, messages=None, context=None):
    """Update a conversation in the database"""
    update_fields = {}
    
    if title is not None:
        update_fields["title"] = title
    
    if messages is not None:
        update_fields["messages"] = messages
    
    if context is not None:
        update_fields["context"] = context
    
    if update_fields:
        conversations_collection.update_one(
            {"user_id": user_id, "conversation_id": conv_id},
            {"$set": update_fields}
        )

def delete_conversation_db(user_id, conv_id):
    """Delete a conversation from the database"""
    conversations_collection.delete_one({"user_id": user_id, "conversation_id": conv_id})

# Function to create a new conversation
def create_new_chat():
    if not st.session_state.authenticated:
        return
    
    conversations, new_id = create_new_conversation_db(st.session_state.user_id)
    st.session_state.conversations = conversations
    st.session_state.current_conversation_id = new_id
    st.rerun()

# Function to switch to a different conversation
def switch_conversation(conv_id):
    st.session_state.current_conversation_id = conv_id
    st.rerun()

# Function to rename current conversation based on first user message
def update_conversation_title(conv_id, user_message):
    """Create a more sensible title for the conversation based on the first user message"""
    current_title = st.session_state.conversations[conv_id]["title"]
    
    if current_title == "New Conversation" and user_message:
        # Extract a concise title from the user's first message
        
        # Remove common question starters
        clean_message = user_message.lower()
        question_starters = ["what is", "how to", "can you", "please tell me about", "i have", "i am experiencing", "tell me about"]
        for starter in question_starters:
            if clean_message.startswith(starter):
                clean_message = clean_message[len(starter):].strip()
        
        # Extract key topic from the message
        words = clean_message.split()
        if len(words) <= 5:
            # If it's already short, use it directly
            title = clean_message.capitalize()
        else:
            # Take first 4-5 meaningful words
            title = " ".join(words[:5]).capitalize()
        
        # Make first letter uppercase
        if title:
            title = title[0].upper() + title[1:]
        
        # Limit length and add ellipsis if needed
        max_length = 30
        title = title if len(title) <= max_length else title[:max_length-3] + "..."
        
        # Update the conversation title
        st.session_state.conversations[conv_id]["title"] = title
        update_conversation_db(st.session_state.user_id, conv_id, title=title)

# Function to delete a conversation
def delete_conversation(conv_id):
    if not st.session_state.authenticated:
        return
        
    if conv_id in st.session_state.conversations:
        # Delete from database
        delete_conversation_db(st.session_state.user_id, conv_id)
        
        # Delete from session state
        del st.session_state.conversations[conv_id]
        
        # If we deleted the current conversation, switch to another one or create new
        if conv_id == st.session_state.current_conversation_id:
            if st.session_state.conversations:
                st.session_state.current_conversation_id = list(st.session_state.conversations.keys())[0]
            else:
                conversations, new_id = create_new_conversation_db(st.session_state.user_id)
                st.session_state.conversations = conversations
                st.session_state.current_conversation_id = new_id
        st.rerun()

# Medication reminder functions
def send_reminder_email(user_email, medicine_name, dosage):
    """Send email reminder for medication"""
    try:
        # Create message
        msg = MIMEMultipart()
        msg['From'] = EMAIL_SENDER
        msg['To'] = user_email
        msg['Subject'] = f"Medication Reminder: Time to take {medicine_name}"
        
        # Message body
        body = f"""
        <html>
        <body>
            <h2>🔔 Medication Reminder</h2>
            <p>It's time to take your medication:</p>
            <p><strong>Medicine:</strong> {medicine_name}</p>
            <p><strong>Dosage:</strong> {dosage}</p>
            <p>Take care of your health!</p>
            <p><em>- Healthcare Assistant</em></p>
        </body>
        </html>
        """
        
        msg.attach(MIMEText(body, 'html'))
        
        # Connect to server and send email
        server = smtplib.SMTP(EMAIL_SMTP_SERVER, EMAIL_PORT)
        server.starttls()
        server.login(EMAIL_SENDER, EMAIL_PASSWORD)
        text = msg.as_string()
        server.sendmail(EMAIL_SENDER, user_email, text)
        server.quit()
        
        print(f"Reminder email sent to {user_email} for {medicine_name}")
        return True
    
    except Exception as e:
        print(f"Failed to send email: {str(e)}")
        return False

def schedule_medication_reminder(reminder_id, user_email, medicine_name, dosage, reminder_time):
    """Schedule a medication reminder"""
    
    # Calculate the notification time (5 minutes before the reminder time)
    notification_time = reminder_time - timedelta(minutes=5)
    
    # Schedule the reminder job
    job_id = f"reminder_{reminder_id}"
    
    # Remove any existing job with this ID
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
    
    # Schedule new job
    scheduler.add_job(
        send_reminder_email,
        'date',
        run_date=notification_time,
        args=[user_email, medicine_name, dosage],
        id=job_id,
        replace_existing=True
    )
    
    print(f"Scheduled reminder for {medicine_name} at {notification_time}")

def load_user_medications(user_id):
    """Load all medication reminders for a user from MongoDB"""
    return list(medication_reminders_collection.find({"user_id": user_id}))

def add_medication_reminder(user_id, user_email, medicine_name, dosage, time_str, recurring_days=None):
    """Add a new medication reminder to the database"""
    reminder_id = str(uuid.uuid4())
    
    # Parse time string into datetime object for today
    today = datetime.datetime.now().date()
    time_parts = time_str.split(":")
    hour = int(time_parts[0])
    minute = int(time_parts[1])
    
    reminder_time = datetime.datetime.combine(today, datetime.time(hour=hour, minute=minute))
    
    # If the time has already passed today, schedule for tomorrow
    if reminder_time < datetime.datetime.now():
        reminder_time = reminder_time + timedelta(days=1)
    
    reminder_data = {
        "reminder_id": reminder_id,
        "user_id": user_id,
        "user_email": user_email,
        "medicine_name": medicine_name,
        "dosage": dosage,
        "time": time_str,
        "recurring_days": recurring_days if recurring_days else [],
        "active": True,
        "created_at": datetime.datetime.now()
    }
    
    # Insert into database
    medication_reminders_collection.insert_one(reminder_data)
    
    # Schedule the reminder
    schedule_medication_reminder(reminder_id, user_email, medicine_name, dosage, reminder_time)
    
    return True

def update_medication_reminder(reminder_id, medicine_name=None, dosage=None, time_str=None, recurring_days=None, active=None):
    """Update an existing medication reminder"""
    update_fields = {}
    
    if medicine_name is not None:
        update_fields["medicine_name"] = medicine_name
    
    if dosage is not None:
        update_fields["dosage"] = dosage
    
    if time_str is not None:
        update_fields["time"] = time_str
    
    if recurring_days is not None:
        update_fields["recurring_days"] = recurring_days
    
    if active is not None:
        update_fields["active"] = active
    
    if update_fields:
        medication_reminders_collection.update_one(
            {"reminder_id": reminder_id},
            {"$set": update_fields}
        )
        
        # Re-schedule the reminder if time or active status changed
        if time_str is not None or active is not None:
            reminder = medication_reminders_collection.find_one({"reminder_id": reminder_id})
            
            if reminder and reminder.get("active", False):
                # Parse time string into datetime object for today
                today = datetime.datetime.now().date()
                time_parts = reminder["time"].split(":")
                hour = int(time_parts[0])
                minute = int(time_parts[1])
                
                reminder_time = datetime.datetime.combine(today, datetime.time(hour=hour, minute=minute))
                
                # If the time has already passed today, schedule for tomorrow
                if reminder_time < datetime.datetime.now():
                    reminder_time = reminder_time + timedelta(days=1)
                
                # Schedule the reminder
                schedule_medication_reminder(
                    reminder_id, 
                    reminder["user_email"], 
                    reminder["medicine_name"], 
                    reminder["dosage"], 
                    reminder_time
                )
            else:
                # Remove any existing scheduled job if reminder is inactive
                job_id = f"reminder_{reminder_id}"
                if scheduler.get_job(job_id):
                    scheduler.remove_job(job_id)
        
        return True
    
    return False

def delete_medication_reminder(reminder_id):
    """Delete a medication reminder"""
    medication_reminders_collection.delete_one({"reminder_id": reminder_id})
    
    # Remove any existing scheduled job
    job_id = f"reminder_{reminder_id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
    
    return True

# Main UI components
def render_login_page():
    st.title("🏥 Healthcare Assistant - Login")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("Login")
        with st.form("login_form"):
            email = st.text_input("Email", key="login_email")
            password = st.text_input("Password", type="password", key="login_password")
            submit = st.form_submit_button("Login")
            
            if submit:
                if not email or not password:
                    st.error("Please enter both email and password")
                else:
                    success, user_id_or_error = login_user(email, password)
                    if success:
                        st.session_state.authenticated = True
                        st.session_state.user_id = user_id_or_error
                        st.session_state.user_email = email
                        st.session_state.page = "main"
                        
                        # Load user conversations
                        conversations, current_id = load_user_conversations(user_id_or_error)
                        st.session_state.conversations = conversations
                        st.session_state.current_conversation_id = current_id
                        
                        st.success("Login successful!")
                        st.rerun()
                    else:
                        st.error(user_id_or_error)
    
    with col2:
        st.subheader("Sign Up")
        with st.form("signup_form"):
            email = st.text_input("Email", key="signup_email")
            password = st.text_input("Password", type="password", key="signup_password")
            confirm_password = st.text_input("Confirm Password", type="password", key="confirm_password")
            submit = st.form_submit_button("Sign Up")
            
            if submit:
                if not email or not password or not confirm_password:
                    st.error("Please fill in all fields")
                elif password != confirm_password:
                    st.error("Passwords do not match")
                else:
                    success, result = signup_user(email, password)
                    if success:
                        st.success("Account created successfully! You can now log in.")
                    else:
                        st.error(result)

def render_medication_page():
    st.title("💊 Medication Reminders")
    
    # Get user medications
    medications = load_user_medications(st.session_state.user_id)
    
    # Create two sections - one for adding new reminders, one for viewing existing ones
    col1, col2 = st.columns([1, 2])
    
    with col1:
        st.subheader("Add Medication Reminder")
        
        # Form for adding or editing medication
        with st.form("medication_form", clear_on_submit=True):
            if st.session_state.medication_edit_mode:
                # Get the reminder being edited
                edit_reminder = next((r for r in medications if r["reminder_id"] == st.session_state.medication_edit_id), None)
                form_title = "Edit Medication Reminder"
                submit_label = "Update Reminder"
                
                medicine_name = st.text_input("Medicine Name", value=edit_reminder.get("medicine_name", ""))
                dosage = st.text_input("Dosage (e.g., 1 pill, 5ml)", value=edit_reminder.get("dosage", ""))
                time_input = st.text_input("Time (24-hour format, HH:MM)", value=edit_reminder.get("time", ""))
                
                days_of_week = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
                recurring = edit_reminder.get("recurring_days", [])
                selected_days = st.multiselect("Recurring Days (Optional)", days_of_week, default=recurring)
                
                active = st.checkbox("Active", value=edit_reminder.get("active", True))
            else:
                form_title = "Add New Reminder"
                submit_label = "Add Reminder"
                
                medicine_name = st.text_input("Medicine Name")
                dosage = st.text_input("Dosage (e.g., 1 pill, 5ml)")
                time_input = st.text_input("Time (24-hour format, HH:MM)")
                
                days_of_week = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
                selected_days = st.multiselect("Recurring Days (Optional)", days_of_week)
                
                active = st.checkbox("Active", value=True)
            
            submit_button = st.form_submit_button(submit_label)
            
            if submit_button:
                if not medicine_name or not time_input:
                    st.error("Please fill in medicine name and time")
                else:
                    # Validate time format
                    try:
                        hour, minute = map(int, time_input.split(":"))
                        if not (0 <= hour <= 23 and 0 <= minute <= 59):
                            st.error("Invalid time format. Please use HH:MM in 24-hour format.")
                        else:
                            if st.session_state.medication_edit_mode:
                                # Update existing reminder
                                success = update_medication_reminder(
                                    st.session_state.medication_edit_id,
                                    medicine_name=medicine_name,
                                    dosage=dosage,
                                    time_str=time_input,
                                    recurring_days=selected_days,
                                    active=active
                                )
                                if success:
                                    st.success("Medication reminder updated successfully!")
                                    st.session_state.medication_edit_mode = False
                                    st.session_state.medication_edit_id = None
                                    st.rerun()
                                else:
                                    st.error("Failed to update reminder")
                            else:
                                # Add new reminder
                                success = add_medication_reminder(
                                    st.session_state.user_id,
                                    st.session_state.user_email,
                                    medicine_name,
                                    dosage,
                                    time_input,
                                    selected_days
                                )
                                if success:
                                    st.success("Medication reminder added successfully!")
                                    st.rerun()
                                else:
                                    st.error("Failed to add reminder")
                    except ValueError:
                        st.error("Invalid time format. Please use HH:MM in 24-hour format.")
        
        # Cancel button for edit mode
        if st.session_state.medication_edit_mode:
            if st.button("Cancel Edit"):
                st.session_state.medication_edit_mode = False
                st.session_state.medication_edit_id = None
                st.rerun()
    
    with col2:
        st.subheader("Your Medication Reminders")
        
        if not medications:
            st.info("No medication reminders. Add one to get started!")
        else:
            for medication in sorted(medications, key=lambda x: x.get("time", "00:00")):
                with st.container():
                    col1, col2, col3 = st.columns([3, 1, 1])
                    
                    with col1:
                        reminder_id = medication["reminder_id"]
                        medicine_name = medication["medicine_name"]
                        dosage = medication.get("dosage", "")
                        time_str = medication.get("time", "")
                        active = medication.get("active", True)
                        recurring = medication.get("recurring_days", [])
                        
                        status = "🟢 Active" if active else "⚪ Inactive"
                        recurring_text = f" on {', '.join(recurring)}" if recurring else " daily"
                        
                        st.markdown(f"**{medicine_name}** ({dosage}) - {time_str} {status}")
                        st.caption(f"Scheduled{recurring_text}")
                    
                    with col2:
                        if st.button("Edit", key=f"edit_{reminder_id}"):
                            st.session_state.medication_edit_mode = True
                            st.session_state.medication_edit_id = reminder_id
                            st.rerun()
                    
                    with col3:
                        if st.button("Delete", key=f"delete_{reminder_id}"):
                            delete_medication_reminder(reminder_id)
                            st.success("Reminder deleted!")
                            st.rerun()
                    
                    st.divider()

def render_main_app():
    # Create two column layout for the sidebar
    with st.sidebar:
        st.title("ℹ️ Configuration")
        
        # User info
        st.subheader(f"👤 User: {st.session_state.user_email}")
        if st.button("🚪 Logout"):
            logout_user()
        
        if not st.session_state.GOOGLE_API_KEY:
            # Only show this if no API key is found in environment or session state
            api_key = st.text_input(
                "Enter your Google API Key",
                type="password"
            )
            st.caption(
                "Get your API key from [Google AI Studio]"
                "(https://aistudio.google.com/apikey) 🔑"
            )
            if api_key:
                st.session_state.GOOGLE_API_KEY = api_key
                st.success("API Key saved!")
                st.rerun()
        else:
            source = "environment variable" if api_key_from_env else "session"
            st.success(f"API Key is configured from {source}")
            if st.button("🔄 Reset API Key"):
                st.session_state.GOOGLE_API_KEY = None
                st.rerun()
        
        st.divider()
        
        # Navigation
        st.subheader("📋 Navigation")
        
        if st.button("💬 Chat", use_container_width=True):
            st.session_state.page = "chat"
            st.rerun()
            
        if st.button("💊 Medications", use_container_width=True):
            st.session_state.page = "medications"
            st.rerun()
        
        st.divider()
        
        # Chat history section (only show in chat mode)
        if st.session_state.page == "chat" or st.session_state.page == "main":
            st.subheader("💬 Conversations")
            
            # New chat button
            if st.button("➕ New Chat", use_container_width=True, type="primary"):
                create_new_chat()
            
            # Display all conversations
            for conv_id, conv_data in sorted(
                st.session_state.conversations.items(), 
                key=lambda x: x[1]["timestamp"], 
                reverse=True
            ):
                col1, col2 = st.columns([4, 1])
                with col1:
                    # Highlight current conversation
                    if conv_id == st.session_state.current_conversation_id:
                        st.button(
                            f"▶️ {conv_data['title']}", 
                            key=f"chat_{conv_id}",
                            use_container_width=True,
                            disabled=True
                        )
                    else:
                        if st.button(
                            f"{conv_data['title']}", 
                            key=f"chat_{conv_id}",
                            use_container_width=True
                        ):
                            switch_conversation(conv_id)
                with col2:
                    # Delete button
                    if st.button("🗑️", key=f"delete_{conv_id}"):
                        delete_conversation(conv_id)
        
        st.divider()
        
        st.info(
            "This healthcare chatbot helps analyze symptoms and provides information "
            "about potential diseases, precautions, and medicines."
        )
        st.warning(
            "⚠DISCLAIMER: This tool is for educational and informational purposes only. "
            "All analyses should be reviewed by qualified healthcare professionals. "
            "Do not make medical decisions based solely on this analysis."
        )

    # Determine which page to show
    if st.session_state.page == "medications":
        render_medication_page()
    else:  # Default to chat page
        st.session_state.page = "chat"
        render_chat_page()

def render_chat_page():
    # Initialize the medical agent once the API key is configured
    medical_agent = Agent(
        model=Gemini(
            id="gemini-2.0-flash",
            api_key=st.session_state.GOOGLE_API_KEY
        ),
        tools=[DuckDuckGoTools()],
        markdown=True
    ) if st.session_state.GOOGLE_API_KEY else None

    # Initialize voice assistant if it doesn't exist
    if not st.session_state.voice_assistant:
        st.session_state.voice_assistant = VoiceAssistant()

    # Get current conversation
    current_id = st.session_state.current_conversation_id
    current_conversation = st.session_state.conversations[current_id]
    current_messages = current_conversation["messages"]
    current_context = current_conversation["context"]

    st.title("🏥 Healthcare Assistant")

    # Create three tabs for different modes
    tab1, tab2, tab3 = st.tabs([
        "💬 Text Chat Assistant", 
        "🎙️ Voice Assistant", 
        "🔍 Medical Image Analysis"
    ])

    # ================== TAB 1: Text Chat Assistant ==================
    with tab1:
        # Container for chat history (will scroll)
        chat_container = st.container(height=500, border=False)
        
        with chat_container:
            # Display chat history
            for message in current_messages:
                with st.chat_message(message["role"]):
                    st.markdown(message["content"])

        # Fixed input area at bottom
        with st.container():
            st.write("Type your symptoms or ask about medications below.")
            prompt = st.chat_input("Type your message here...", key="text_chat_input")

            if prompt:
                with chat_container.chat_message("user"):
                    st.markdown(prompt)
                
                # Add to chat history
                current_messages.append({"role": "user", "content": prompt})
                update_conversation_db(
                    st.session_state.user_id, 
                    current_id, 
                    messages=current_messages
                )

                if not medical_agent:
                    with chat_container.chat_message("assistant"):
                        st.warning("Please configure your API key in the sidebar to continue")
                else:
                    with chat_container.chat_message("assistant"):
                        with st.spinner("Thinking..."):
                            # Update context
                            if current_context:
                                current_context += f"\nUser: {prompt}"
                            else:
                                current_context = f"User: {prompt}"

                            full_prompt = healthcare_query.format(context=current_context)
                            response = medical_agent.run(full_prompt)
                            st.markdown(response.content)

                            current_context += f"\nAssistant: {response.content}"
                            st.session_state.conversations[current_id]["context"] = current_context
                            current_messages.append({"role": "assistant", "content": response.content})

                            update_conversation_db(
                                st.session_state.user_id, 
                                current_id, 
                                messages=current_messages,
                                context=current_context
                            )

                            if len(current_messages) == 2:
                                update_conversation_title(current_id, prompt)

    # ================== TAB 2: Voice Assistant ==================
    with tab2:
        # Container for chat history (will scroll)
        chat_container = st.container(height=500, border=False)
        
        with chat_container:
            # Display chat history
            for message in current_messages:
                with st.chat_message(message["role"]):
                    st.markdown(message["content"])

        # Fixed input area at bottom
        with st.container():
            st.write("Click the microphone button to speak your query.")
            
            # Voice input section
            if st.session_state.get("is_recording", False):
                if st.button("⏹️ Stop Recording", key="stop_recording", help="Stop recording", use_container_width=True):
                    # Process recording
                    voice_text = st.session_state.voice_assistant.process_voice_query()
                    st.session_state.is_recording = False
                    
                    if voice_text:
                        st.session_state.last_voice_text = voice_text
                        st.rerun()
                    else:
                        st.error("Failed to recognize speech. Please try again.")
            else:
                if st.button("🎤 Start Recording", key="start_recording", help="Start recording", use_container_width=True):
                    if st.session_state.voice_assistant.start_recording():
                        st.session_state.is_recording = True
                        st.rerun()
                    else:
                        st.error("Failed to start recording")

            # Processing status
            if st.session_state.get("is_recording", False):
                st.write("🔄 Processing your query...")

        # If we have voice text, process it
        if st.session_state.get("last_voice_text"):
            prompt = st.session_state.last_voice_text
            del st.session_state.last_voice_text

            with chat_container.chat_message("user"):
                st.markdown(prompt)
            
            # Add to chat history
            current_messages.append({"role": "user", "content": prompt})
            update_conversation_db(
                st.session_state.user_id, 
                current_id, 
                messages=current_messages
            )

            if not medical_agent:
                with chat_container.chat_message("assistant"):
                    st.warning("Please configure your API key in the sidebar to continue")
            else:
                with chat_container.chat_message("assistant"):
                    with st.spinner("Thinking..."):
                        # Update context
                        if current_context:
                            current_context += f"\nUser: {prompt}"
                        else:
                            current_context = f"User: {prompt}"

                        full_prompt = healthcare_query.format(context=current_context)
                        response = medical_agent.run(full_prompt)
                        st.markdown(response.content)

                        current_context += f"\nAssistant: {response.content}"
                        st.session_state.conversations[current_id]["context"] = current_context
                        current_messages.append({"role": "assistant", "content": response.content})

                        update_conversation_db(
                            st.session_state.user_id, 
                            current_id, 
                            messages=current_messages,
                            context=current_context
                        )

                        if len(current_messages) == 2:
                            update_conversation_title(current_id, prompt)

    # ================== TAB 3: Medical Image Analysis ==================
    with tab3:
        # Container for chat history (will scroll)
        chat_container = st.container(height=500, border=False)
        
        with chat_container:
            # Display chat history
            for message in current_messages:
                with st.chat_message(message["role"]):
                    st.markdown(message["content"])

        # Fixed input area at bottom
        with st.container():
            st.write("Upload a medical image for professional analysis")
            
            uploaded_file = st.file_uploader(
                "Upload Medical Image",
                type=["jpg", "jpeg", "png", "dicom"],
                help="Supported formats: JPG, JPEG, PNG, DICOM"
            )

            if uploaded_file is not None:
                col1, col2, col3 = st.columns([1, 2, 1])
                with col2:
                    image = PILImage.open(uploaded_file)
                    width, height = image.size
                    aspect_ratio = width / height
                    new_width = 500
                    new_height = int(new_width / aspect_ratio)
                    resized_image = image.resize((new_width, new_height))
                    
                    st.image(
                        resized_image,
                        caption="Uploaded Medical Image",
                        use_container_width=True
                    )
                    
                    analyze_button = st.button("🔍 Analyze Image", type="primary", use_container_width=True)
            
                if analyze_button:
                    with chat_container.chat_message("user"):
                        st.markdown("I uploaded a medical image for analysis.")
                    
                    with chat_container.chat_message("assistant"):
                        with st.spinner("🔄 Analyzing image... Please wait."):
                            try:
                                if not medical_agent:
                                    st.warning("Please configure your API key in the sidebar to continue")
                                else:
                                    temp_path = "temp_resized_image.png"
                                    resized_image.save(temp_path)
                                    
                                    # Create AgnoImage object
                                    agno_image = AgnoImage(filepath=temp_path)
                                    
                                    # Run analysis
                                    response = medical_agent.run(image_analysis_query, images=[agno_image])
                                    
                                    # Add to chat history
                                    current_messages.append({"role": "user", "content": "I uploaded a medical image for analysis."})
                                    current_messages.append({"role": "assistant", "content": response.content})
                                    
                                    # Update conversation in DB
                                    update_conversation_db(
                                        st.session_state.user_id, 
                                        current_id, 
                                        messages=current_messages
                                    )
                                    
                                    st.markdown("### 📋 Analysis Results")
                                    st.markdown("---")
                                    st.markdown(response.content)
                                    st.markdown("---")
                                    st.caption(
                                        "Note: This analysis is generated by AI and should be reviewed by "
                                        "a qualified healthcare professional."
                                    )
                            except Exception as e:
                                st.error(f"Analysis error: {e}")
            else:
                st.info("👆 Please upload a medical image to begin analysis")

# Medical Image Analysis Query
image_analysis_query = """
You are a medical imaging expert fluent in English, Urdu script, and Roman Urdu (Urdu written in Latin script). Analyze the patient's image concisely with the following structure. Respond in the SAME language format as the user's query if specified, otherwise default to English.

### 1. Image Type & Region
- Identify imaging modality (X-ray/MRI/CT/etc.)
- Specify anatomical region and positioning
- Brief comment on image quality

### 2. Key Findings (LIMIT: 3-4 points)
- List major observations only
- Note significant abnormalities with brief descriptions
- Include only essential measurements 
- Rate severity: Normal/Mild/Moderate/Severe

### 3. Diagnosis (LIMIT: 150 words)
- State primary diagnosis with confidence level
- List maximum 2 differential diagnoses
- Mention only critical or urgent findings

### 4. Patient-Friendly Explanation (LIMIT: 100 words)
- Use simple, clear language
- Explain 1-2 key findings that matter most
- Address the most common patient concern

### 5. References (LIMIT: 2 only)
- Use DuckDuckGo to find 1 recent medical study
- Include 1 relevant treatment guideline

For Urdu script responses:
- Use the same structure but translate all content to Urdu script
- Include specialized medical terms in both Urdu and English (in brackets) where appropriate

For Roman Urdu responses:
- Use the same structure but respond in Roman Urdu (Urdu in Latin script)
- Include specialized medical terms in both Roman Urdu and English (in brackets)
- Use common Roman Urdu spelling conventions that Pakistani users would understand

Keep your total response under 500 words. Be direct and focused on clinical significance.
Include this disclaimer at the end in the appropriate language format:
- English: "This analysis is for educational purposes only. Please consult with a qualified healthcare professional."
- Urdu: "یہ تجزیہ صرف تعلیمی مقاصد کے لیے ہے۔ براہ کرم ایک قابل صحت کے پیشہ ور سے مشورہ کریں۔"
- Roman Urdu: "Yeh tajziya sirf taleemi maqasid ke liye hai. Barah-e-karam aik qabil sehat ke pesha war se mashwara karein."
"""

# Healthcare Query with Context Awareness
healthcare_query = """
You are a healthcare assistant fluent in English, Urdu, and Roman Urdu (Urdu written in Latin script). Detect the language of the user's query and respond in the SAME language format. Focus exclusively on health-related topics and decline to answer non-health queries politely.

If the user describes symptoms, structure your response as follows:

### Potential Diseases
- List EXACTLY 2 potential conditions that best match the described symptoms
- Provide only a 1-2 sentence explanation for each
- Indicate probability (e.g., highly likely, possible)

### Recommended Precautions
- List 2-3 specific precautions the person should take
- Be brief and direct
- Mention when to seek professional medical help

### Possible Medications
- Suggest EXACTLY 2 appropriate over-the-counter medications
- Provide only the most essential information about each
- Specify frequency (e.g., once daily, twice daily, with meals) rather than hourly timing
- Include standard dosage in a single line
- Mention only critical warnings

For Urdu script queries:
- Maintain the same structure but respond completely in Urdu script
- Use simple, commonly understood medical terms in Urdu
- Include English medication names alongside Urdu descriptions when appropriate

For Roman Urdu queries:
- Respond completely in Roman Urdu (Urdu in Latin script)
- Use common Roman Urdu spelling conventions that Pakistani users would understand
- Include English medication names when appropriate

IMPORTANT: 
1. Use the DuckDuckGo search tool to ensure your information is accurate.
2. Maintain conversation context. Reference previous symptoms if relevant.
3. Include this disclaimer in the appropriate language: 
   - English: "This is not a diagnosis. Please consult a healthcare professional."
   - Urdu: "یہ تشخیص نہیں ہے۔ براہ کرم صحت کے ماہر سے مشورہ کریں۔"
   - Roman Urdu: "Yeh tashkhees nahi hai. Barah-e-karam sehat ke mahir se mashwara karein."
4. If the user query is not health-related, politely decline and suggest they ask a health question instead.
5. Keep your total response under 300 words.
6. ALWAYS respond in the SAME language format as the user's query (English, Urdu script, or Roman Urdu).

CONVERSATION CONTEXT: {context}
"""

def is_medication_query(user_input):
    """Check if the query is about medication information in English, Urdu or Roman Urdu"""
    # English medication keywords
    medication_keywords = ["medicine", "medication", "drug", "pill", "tablet", "capsule", "dose", "prescription"]
    information_keywords = ["about", "info", "information", "tell me", "what is", "side effect", "how to take"]
    
    # Urdu script medication keywords
    urdu_medication_keywords = ["دوا", "ادویات", "گولی", "ٹیبلٹ", "کیپسول", "خوراک", "نسخہ"]
    urdu_information_keywords = ["کے بارے میں", "معلومات", "بتائیں", "کیا ہے", "ضمنی اثرات", "کیسے لیں"]
    
    # Roman Urdu medication keywords
    roman_urdu_medication_keywords = ["dawa", "dawai", "adwiyat", "goli", "tablet", "capsule", "khurak", "nuskha"]
    roman_urdu_information_keywords = ["ke bare mein", "malomat", "batain", "kya hai", "side effects", "zimni asraat", "kaise lein", "kese len"]
    
    user_input_lower = user_input.lower()
    
    # Check English terms
    has_medication_term = any(word in user_input_lower for word in medication_keywords)
    has_info_request = any(word in user_input_lower for word in information_keywords)
    direct_english_question = "what" in user_input_lower and any(med in user_input_lower for med in medication_keywords)
    
    # Check Urdu script terms
    has_urdu_medication_term = any(word in user_input for word in urdu_medication_keywords)
    has_urdu_info_request = any(word in user_input for word in urdu_information_keywords)
    
    # Check Roman Urdu terms
    has_roman_medication_term = any(word in user_input_lower for word in roman_urdu_medication_keywords)
    has_roman_info_request = any(phrase in user_input_lower for phrase in roman_urdu_information_keywords)
    direct_roman_question = "kya" in user_input_lower and any(med in user_input_lower for med in roman_urdu_medication_keywords)
    
    # Return True if any language's criteria are met
    return ((has_medication_term and has_info_request) or 
            direct_english_question or 
            (has_urdu_medication_term and has_urdu_info_request) or
            (has_roman_medication_term and has_roman_info_request) or
            direct_roman_question)

# Function to schedule all existing reminders when the app starts
def initialize_reminders():
    """Initialize all active reminders when the app starts"""
    all_reminders = medication_reminders_collection.find({"active": True})
    
    for reminder in all_reminders:
        try:
            # Parse time string into datetime object for today
            today = datetime.datetime.now().date()
            time_parts = reminder["time"].split(":")
            hour = int(time_parts[0])
            minute = int(time_parts[1])
            
            reminder_time = datetime.datetime.combine(today, datetime.time(hour=hour, minute=minute))
            
            # If the time has already passed today, schedule for tomorrow
            if reminder_time < datetime.datetime.now():
                reminder_time = reminder_time + timedelta(days=1)
            
            # Schedule the reminder
            schedule_medication_reminder(
                reminder["reminder_id"], 
                reminder["user_email"], 
                reminder["medicine_name"], 
                reminder.get("dosage", ""), 
                reminder_time
            )
        except Exception as e:
            print(f"Error scheduling reminder {reminder['reminder_id']}: {str(e)}")

# Main app logic
def main():
    # Initialize reminders when app starts
    initialize_reminders()
    
    # Render the appropriate page based on auth state
    if not st.session_state.authenticated:
        render_login_page()
    else:
        render_main_app()

# Run the main app
if __name__ == "__main__":
    main()