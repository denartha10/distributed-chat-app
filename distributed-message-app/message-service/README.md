### Instructions

- Git clone
- Create a virtual environment using `python3 -m venv .venv`
- Start virtaul environment `source .venv/bin/activate`
- Run with `make run` if that doesn't work then try `uvicorn main:app --reload`
- Visit the site on `http://localhost:8000` or `http://127.0.0.1:8000`

### Things I Need

#### User functionality
- [x] I need authentication. I do not want anonymous users
- [x] I need a way to extract that information about a user so they can message using there name
- [ ] I also maybe need a way to add friends so you can search users to chat to

#### chat functionality
- [x] I need a way to open up individual to individual chats without fear of it crossing in to someone elses
- [x] I need persistent chat rooms and groups so I need a database
- [x] persistence messaging
- [x] ability to delete messages
- [ ] ability to edit messages
- [ ] ability to send pictures woukd be nice but would require a media service

#### Architecture
At the moment the project exists as a single fastapi app we should be able to have multiple instances on the same or different machines

##### Required 
- [x] Main Application (user creation and messaging) 
- [ ] Async Messaging Service (rabbit mq)
- [ ] Database persistence layer (seperate server)
- [x] Load balancing solution
- [ ] Security permissions on database

##### Nice to have
- [ ] Media service
- [ ] Notification Service (Can notify a user of new chats atm but nothing else)

#### BUGS

- [ ] The fake broker only allows one connection per user account.. in rabbit mq this should be resolved
