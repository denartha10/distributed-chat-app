import os
from fastapi import FastAPI, HTTPException, Request, Depends, Response, status, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from datetime import datetime
from contextlib import asynccontextmanager

from pydantic import BaseModel
from sqlalchemy import Column, ForeignKey, Integer, String, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker, Session

import hashlib
import uuid
from typing import Annotated

from starlette.status import HTTP_200_OK

import aio_pika
from aio_pika import ExchangeType, Message, DeliveryMode

from fastapi.templating import Jinja2Templates


############################################### DATABASE STUFF ######################################################

ACCESS_TOKEN_EXPIRE_MINUTES = 30

# Get the DATABASE_URL from environment variables
db_url = os.getenv("DATABASE_URL", "sqlite:///./application.db")  # Default to SQLite if not set
engine = create_engine(db_url)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Channel(Base):
    __tablename__ = "channels"
    channel_id = Column(String, primary_key=True, index=True)
    created_at = Column(String, default=datetime.utcnow().isoformat)

    # Relationship to ChannelMembers
    members = relationship("ChannelMembers", back_populates="channel", cascade="all, delete-orphan")

    # Relationship to Messages
    messages = relationship("Messages", back_populates="channel", cascade="all, delete-orphan")



# TODO: CHANNEL_ID + USER SHOULD BE PRIMARY KEY
# NOTE: Moved channel name to channel members because the member shoud have ownership
class ChannelMembers(Base):
    __tablename__ = "channel_members"
    id = Column(Integer, primary_key=True, autoincrement=True)
    channel_id = Column(String, ForeignKey("channels.channel_id"), nullable=False)
    channel_nickname = Column(String, nullable=False)
    user_id = Column(String, ForeignKey("users.username"), nullable=False)
    joined_at = Column(String, default=datetime.utcnow().isoformat)

    # Relationships
    channel = relationship("Channel", back_populates="members")
    user = relationship("User", back_populates="channels")


class Messages(Base):
    __tablename__ = "messages"
    message_id = Column(String, primary_key=True, index=True)
    channel_id = Column(String, ForeignKey("channels.channel_id"), nullable=False)
    sender_id = Column(String, ForeignKey("users.username"), nullable=False)
    content = Column(String)
    sent_at = Column(String, default=datetime.utcnow().isoformat)

    # Relationship with Channel
    channel = relationship("Channel", back_populates="messages")

    # Relationship with User (Sender)
    sender = relationship("User", back_populates="messages")


class User(Base):
    __tablename__ = "users"
    username = Column(String, primary_key=True, index=True)
    email = Column(String, unique=True, nullable=False)
    name = Column(String)
    surname = Column(String)
    password = Column(String)

    # Relationships
    channels = relationship("ChannelMembers", back_populates="user", cascade="all, delete-orphan")
    messages = relationship("Messages", back_populates="sender", cascade="all, delete-orphan")




class SessionToken(Base):
    __tablename__ = "session_tokens"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, ForeignKey("users.username"), nullable=False)
    token = Column(String, unique=True, index=True)
    created_at = Column(String, default=datetime.utcnow().isoformat)

    user = relationship("User")

Base.metadata.create_all(bind=engine)

#####################################################################################################################


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def verify_password(plain_password: str, hashed_password: str):
    return hashlib.sha256(plain_password.encode()).hexdigest() == hashed_password

def fetch_user(db: Session, username: str):
    return db.query(User).filter(User.username == username).first()

def authenticate_user(username: str, password: str, db: Session):
    user = fetch_user(db, username)
    if user and verify_password(password, str(user.password)):
        return user
    return None

def create_session_token(user_id: int, db: Session):
    token = hashlib.sha256(f"{user_id}-{datetime.utcnow().isoformat()}".encode()).hexdigest()
    session_token = SessionToken(user_id=user_id, token=token)
    db.add(session_token)
    db.commit()
    return token

def get_user_from_session(token: str, db: Session):
    session = db.query(SessionToken).filter(SessionToken.token == token).first()
    if session:
        return session.user
    return None



def fetch_user_channels(db: Session, username: str):
    return db.query(ChannelMembers).filter(ChannelMembers.user_id == username).all()

def fetch_channel(db: Session, username: str, id: str):
    return db.query(ChannelMembers)\
            .filter(ChannelMembers.user_id == username)\
            .filter(ChannelMembers.channel_id == id)\
            .first()

def create_channel(db: Session, channel_id: str):
    new_channel = Channel(channel_id = channel_id)

    db.add(new_channel)
    db.commit()

def create_channel_member(db: Session, channel_id: str, username: str, channel_nickname):
    new_member = ChannelMembers(channel_id = channel_id, user_id = username, channel_nickname = channel_nickname)

    db.add(new_member)
    db.commit()


def fetch_channel_messages(db: Session, id: str):
    return db.query(Messages)\
            .join(Channel)\
            .filter(Channel.channel_id == id)\
            .all()

def create_message(db: Session, message_id: str, channel_id: str, sender_id: str, content: str):
    new_message = Messages(message_id = message_id, channel_id = channel_id, sender_id=sender_id, content=content)

    db.add(new_message)
    db.commit()
    db.refresh(new_message)

    return new_message


def get_all_members_for_channel(db: Session, channel_id: str) -> list[Column[str]]:
    """
    Returns all user_ids (i.e., usernames) for every member in the specified channel.
    """
    # Fetch all rows in ChannelMembers for the given channel_id
    members = db.query(ChannelMembers).filter(ChannelMembers.channel_id == channel_id).all()

    # Extract just the user_id from each record
    return [
        m.user_id for m in members
    ]

################################# FAST API APP INITIALISATION ###################################

@asynccontextmanager
async def lifespan(app: FastAPI):
    await mbroker.connect()
    yield
    if mbroker.connection:
        await mbroker.connection.close()

app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")


################################# JINJA2 SETUP ###################################

def iso_to_time(iso_string):
    try:
        dt = datetime.fromisoformat(iso_string)
        return dt.strftime("%H:%M")
    except ValueError:
        return "Invalid date"

templates = Jinja2Templates(directory="templates")
templates.env.filters["iso_to_time"] = iso_to_time


## OKAY LET ME THINK THROUGH THE ORDER OF THINGS
# - Loading the home page should do the following
    # fetch all channels and use those in the home page

# - if i am on the home page then i should be able to create a new channel. 
    # i choose a user to open a channel with and hit create (or maybe don't create until first message sent)
    # on the server side this post to channels/<new_channel_receiver> creates a new channel and returns that page and maybe pushes the new id url in to the page

# - the new page will establish a connection to the server endpoint for messaging
    # - i don't know how to send a message onwards

# - the post message will post a new message to the send message endpoint which must persist the message to a database
    # - and broadcast the message to members of the channel

################## RABBITMQ ####################


class RabbitMQClient:

    def __init__(self, templates: Jinja2Templates, amqp_url: str = "amqp://guest:guest@localhost/", exchange_name: str = "chat_exchange"):
        self.amqp_url = amqp_url
        self.exchange_name = exchange_name
        self.templates = templates

        self.connection = None
        self.channel = None
        self.exchange = None

        # We'll store (channel_id, username) -> queue so we can do .disconnect()
        self.queues = {}

    async def connect(self):
        """
        Called once at startup (@app.on_event("startup")) to connect and declare the exchange.
        """
        self.connection = await aio_pika.connect_robust(
                self.amqp_url)
        self.channel = await self.connection.channel()
        await self.channel.set_qos(prefetch_count=0)


        self.exchange = await self.channel.declare_exchange(
            self.exchange_name,
            ExchangeType.TOPIC,
            durable=True
        )

    def get_all_members_for_channel(self, channel_id: str) -> list[str]:
        with SessionLocal() as db:
            members = db.query(ChannelMembers).filter(ChannelMembers.channel_id == channel_id).all()
            return [
                    m.user_id for m in members
                ]

    def render_add_message(self, receiver_username: str, message, channel_id: str) -> str:

        rendered_html = self.templates.get_template("message.html").render(
            receiver=receiver_username,  # used in your message.html for "Me vs. Other"
            message=message,
            chan=channel_id,
        )
        # SSE event format
        return f"event:messageadded\ndata: {rendered_html.replace('\n', '')}\n\n"

    async def publish(self, channel_id: str, event: str, message=None):
        """
        Multi-publish approach:
        1) Get all usernames in channel_id.
        2) For each user, render the snippet with the correct "receiver".
        3) Publish it with routing_key=f"{channel_id}.{that_user}.{event}".
        """
        if not self.exchange:
            raise RuntimeError("RabbitMQ exchange not declared. Did you forget to call connect()?")

        all_users = self.get_all_members_for_channel(channel_id)

        for user_name in all_users:
            # Build SSE text
            if event == "add":
                sse_text = self.render_add_message(user_name, message, channel_id)
            elif event == "delete":
                sse_text = "event:messagedeleted\ndata:\n\n"
            elif event == "edit":
                sse_text = "event:messageedited\ndata:\n\n"
            elif event == "refresh":
                sse_text = "event:refresh\ndata:\n\n"
            else:
                sse_text = f"event:unknown\ndata:{event}\n\n"

            msg = Message(
                body=sse_text.encode("utf-8"),
                delivery_mode=DeliveryMode.NOT_PERSISTENT
            )

            # e.g. "f'{channel_id}.{user_name}.{event}'" => "12345.bob.add"
            if channel_id is None:
                routing_key = f"{user_name}.{event}"
            else:
                routing_key = f"{channel_id}.{user_name}.{event}"
            print(f"Publishing to {routing_key} with event={event}")
            await self.exchange.publish(msg, routing_key=routing_key)

    async def subscribe(self, username: str, channel_id: str):
        """
        Return an async generator that yields SSE lines for (channel_id, username).
        We bind "channel_id.username.{event}" so that only messages specifically
        rendered for this user appear.
        """
        if not self.channel or not self.exchange:
            raise RuntimeError("RabbitMQ channel/exchange not connected or declared.")

        # queue_name = f"{channel_id}_{username}"

        queue_name = f"{channel_id}_{username}"
        # If we haven't declared it yet, do so
        if (channel_id, username) not in self.queues:

            queue = await self.channel.declare_queue(
                name=queue_name,
                exclusive=True,
                # auto_delete=True,
                durable=False,
            )
            # We bind for each event type:
            for evt in ["add", "delete", "edit", "refresh"]:
                rk = f"{channel_id}.{username}.{evt}"
                print(f"Binding queue {queue_name} to {rk}")
                await queue.bind(self.exchange, routing_key=rk)

            self.queues[(channel_id, username)] = queue
        else:
            queue = self.queues[(channel_id, username)]

        # SSE generator
        async with queue.iterator() as queue_iter:
            try:
                async for raw_msg in queue_iter:
                    async with raw_msg.process():
                        yield raw_msg.body.decode("utf-8")
            finally:
                print(f"[subscribe] SSE ended for {queue_name}")



    async def subscribe_app(self, username: str):
        """
        A separate subscription method for user-level (app-level) events.
        For instance, 'refresh' events that say 'a new channel is available.'
        Returns an async generator that yields SSE lines for this user.
        """
        if not self.channel or not self.exchange:
            raise RuntimeError("RabbitMQ channel/exchange not connected or declared.")

        # We'll store the queue in a different dict key, or reuse self.queues with a different index.
        # For instance: (username, "app") instead of (channel_id, username).
        queue_key = (username, "app")

        # Create a unique queue name for user-level events
        queue_name = f"user_app_{username}"

        # If we haven't already declared this queue, do it now
        if queue_key not in self.queues:
            queue = await self.channel.declare_queue(
                name=queue_name,
                exclusive=True,  # ephemeral
                # auto_delete=True,  # queue removed automatically when no consumers
                durable=False
            )
            # For example, you might only care about "refresh" events for the user
            # or any other event type you want at the user level
            for evt in ["refresh"]:
                # We'll publish with routing_key = f"{username}.{evt}"
                rk = f"user_app_{username}.{evt}"
                await queue.bind(self.exchange, routing_key=rk)

            self.queues[queue_key] = queue
        else:
            queue = self.queues[queue_key]

        # This pattern yields SSE lines from the queue
        async with queue.iterator() as queue_iter:
            try:
                async for raw_msg in queue_iter:
                    async with raw_msg.process():
                        yield raw_msg.body.decode("utf-8")
            finally:
                print(f"[subscribe_app] SSE ended for {queue_name}")

    async def publish_app_event(self, username: str, event: str, data=""):
        if not self.exchange:
            raise RuntimeError("Broker not connected yet.")

        # For 'refresh', maybe we just do a small SSE message
        if event == "refresh":
            sse_text = "event:refresh\ndata:\n\n"
        else:
            sse_text = f"event:unknown\ndata:{event}\n\n"

        msg = Message(body=sse_text.encode("utf-8"))
        routing_key = f"user_app_{username}.{event}"
        await self.exchange.publish(msg, routing_key=routing_key)

    async def disconnect_app(self, username: str):
        """
        Cleanup method specifically for user-level app queues.
        """
        queue_key = (username, "app")
        queue = self.queues.pop(queue_key, None)
        if queue:
            try:
                await queue.delete(if_unused=False, if_empty=False)
            except Exception:
                pass



mbroker = RabbitMQClient(
    amqp_url="amqp://guest:guest@rabbitmq/",
    exchange_name="chat_exchange",
    templates=templates
)


################################# HOME HANDLER ###################################






@app.get("/", response_class=HTMLResponse)
async def home(request: Request, db: Session = Depends(get_db)):
    user = request.state.user
    users_channels = fetch_user_channels(db, user.username)
    return templates.TemplateResponse(name="index.html", request=request, context={"user": user, "channels": users_channels})





################################## MIDDLEWARE ####################################
@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    if request.url.path not in ["/login", "/register"] and not request.url.path.startswith("/static"):
        session_token = request.cookies.get("session_token")
        if not session_token:
            return RedirectResponse(url="/login")
        with SessionLocal() as db:
            user = get_user_from_session(session_token, db)
            if not user:
                return RedirectResponse(url="/login")
            request.state.user = user
    return await call_next(request)


################################## REGISTER HANDLER AND MODEL ####################################

class UserCreate(BaseModel):
    username: str
    name: str
    surname: str
    email: str
    password: str
    confirmpassword: str

@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse(name="register.html", request=request, context={})


@app.post("/register")
async def register(request: Request, user: Annotated[UserCreate, Form()], db: Session = Depends(get_db)):
    existing_user = fetch_user(db, user.username)
    if existing_user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Username already taken")

    if user.password != user.confirmpassword:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Passwords did not match")

    hashed_password = hashlib.sha256(user.password.encode()).hexdigest()
    new_user = User( username=user.username, password=hashed_password, name=user.name, surname=user.surname, email=user.email)

    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    return templates.TemplateResponse(name="register.html", request=request, context={"success": True})


################################## LOGIN HANDLERS ####################################

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(name="login.html", request=request, context={})

@app.post("/login")
async def login(username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = authenticate_user(username, password, db)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")

    session_token = create_session_token(user.username, db)
    response = JSONResponse({"message": "success"})
    response.set_cookie(key="session_token", value=session_token, httponly=True)
    response.headers["HX-Location"] = "/"
    return response


################################# CHANNEL HANDLERS ###################################

@app.get("/channels/@me/friends")
async def get_friends_page(request: Request, db: Session = Depends(get_db)):
    user = request.state.user
    users_channels = fetch_user_channels(db, user.username)

    return templates.TemplateResponse(
            name="friends.html", 
            request=request, 
            context={ "user": user, "channels": users_channels, })


@app.get("/channels/get/list")
async def get_channel_list(request: Request, db: Session = Depends(get_db)):
    users_channels = fetch_user_channels(db, request.state.user.username)

    return templates.TemplateResponse(
            name="channelnames.html",
            request=request,
            context={
                "channels": users_channels,
            })

@app.get("/channels/@me/{channelid}")
async def channel(request: Request, channelid: str, db: Session = Depends(get_db)):
    user = request.state.user
    active_channel = fetch_channel(db, user.username, channelid)
    users_channels = fetch_user_channels(db, user.username)
    messages = fetch_channel_messages(db, channelid)

    if not active_channel:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat does not exist")

    return templates.TemplateResponse(
            name="index.html", 
            request=request, 
            context={
                "user": user, 
                "messages" : messages,
                "channels": users_channels, 
                "active_channel": active_channel
            })


@app.post("/channels/@me/{username}")
async def new_channel(request: Request, username: str, db: Session = Depends(get_db)):
    channel_id = str(uuid.uuid4()) 

    verified_username = fetch_user(db, username)
    if not verified_username:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"User {username} does not exist")

    create_channel(db, channel_id)
    create_channel_member(db, channel_id, username, request.state.user.username)
    create_channel_member(db, channel_id, request.state.user.username, username)
    print(username, "is a member of", channel_id)
    await mbroker.publish_app_event(username, "refresh")

    r = Response(status_code=status.HTTP_200_OK, headers={"HX-Location" : f"/channels/@me/{channel_id}"})
    return r


@app.get("/users")
async def get_users(request: Request, user_string: str, db: Session = Depends(get_db)):
    requestinguser = request.state.user.username
    if len(user_string) < 1:
        return  templates.TemplateResponse(name="userlist.html", request = request, context = {"users": []})

    users = db.query(User)\
            .filter(
                    User.username.contains(user_string), 
                    User.username != requestinguser
            ).all()

    return  templates.TemplateResponse(name="userlist.html", request = request, context = {"users": users})



################################# MESSAGE HANDLERS ###################################

@app.post("/channels/{channel_id}/message")
async def send_message(request: Request, channel_id: str, message = Form(...), db: Session = Depends(get_db)):
    username = request.state.user.username
    channel = fetch_channel(db, username, channel_id)

    message = create_message(db, str(uuid.uuid4()), channel_id, username, message)

    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    await mbroker.publish(channel_id, "add", message)
    return {"status": "Message sent"}


@app.delete("/channels/{channel_id}/message/{message_id}")
async def delete_message(request: Request, channel_id: str, message_id: str, db: Session = Depends(get_db)):
    # this is not a secure check but 

    user = request.state.user
    m = db.query(Messages).filter_by(message_id=message_id, channel_id=channel_id, sender_id=user.username).first()
    db.delete(m)
    db.commit()

    await mbroker.publish(channel_id, "delete", m)
    return Response(status_code=HTTP_200_OK)


################################# SSE HANDLERS ###################################

# NOT SURE YET IF BEST IDEA IN THE WORLD BUT IF A USER HAS A TOPIC THEN WE CAN PUSH EVENTS TO THEM ONLY
@app.get("/sse/app")
async def sse_app_endpoint(request: Request, db: Session = Depends(get_db)):
    username = request.state.user.username if request.state.user.username else ""
    user = fetch_user(db, username)

    if not user:
        raise HTTPException(status_code=404, detail="Could not find user found")

    event_generator = mbroker.subscribe_app(username)

    return StreamingResponse(event_generator, media_type="text/event-stream")


@app.get("/sse/{channelid}")
async def sse_channel_endpoint(request: Request, channelid: str, db: Session = Depends(get_db)):
    username = request.state.user.username
    channel = fetch_channel(db, username, channelid)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")


    event_generator = mbroker.subscribe(username,channelid)

    return StreamingResponse(event_generator, media_type="text/event-stream")


