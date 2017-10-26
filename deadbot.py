
import urllib.request
import http.server
import json
import codecs
import shlex
import argparse
from datetime import datetime, timedelta
import collections
import pickle
import os
import threading
import contextlib

HOOK = "https://hooks.slack.com/services/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"
TOKEN = "NZLjPrrU9rlVvdHsrILIsD4J"

def post(msg : str):
    req = urllib.request.Request(HOOK, data=json.dumps({"text": msg}).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=15) as res:
        if res.getcode() == 200:
            return
        else:
            raise IOError("Scary reponse from Slack", res)

class DeadlineRequestHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers["Content-Length"])
        data = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))

        if data["token"] != [TOKEN]:
            self.send_response(400)
            self.end_headers()
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()

        uid = data["user_id"][0]
        args = ns(shlex.split(data["text"][0]))

        with DATA.lock():
            try:
                response = handle(uid, args)
            except Exception as e:
                import traceback
                traceback.print_exc()
                response = Ephemeral("*" + type(e).__name__ + "*: " + str(e))
        assert DATA.unlocked()

        if response:
            self.wfile.write(json.dumps({
                "response_type": "in_channel" if isinstance(response, Response) else "ephemeral",
                "text": str(response)
            }).encode("utf-8"))
        else:
            self.wfile.write(json.dumps({"response_type": "in_channel"}).encode("utf-8"))

def start_server(port:int = 57005):
    httpd = http.server.HTTPServer(("", port), DeadlineRequestHandler)
    httpd.serve_forever()

class Ephemeral(str): pass
class Response(str): pass

class ns:
    def __init__(self, args):
        self.args = args

    def match(self, *pat):
        out = []
        if len(pat) != len(self.args):
            return False
        for val, arg in zip(pat, self.args):
            if isinstance(val, list):
                out.append(arg)
            elif val == arg:
                continue
            else:
                return False
        self.vars = out
        return True

Conference = collections.namedtuple("Conference", ["when", "who", "announcements"])

class Deadlines:
    def __init__(self):
        self.deadlines = None
        self._deadlines = {}
        self._lock = threading.Lock()

    @contextlib.contextmanager
    def lock(self):
        self._lock.acquire()
        self.deadlines = self._deadlines
        yield
        self._deadlines = self.deadlines
        self.deadlines = None
        self._lock.release()

    def unlocked(self):
        return not self.deadlines

    def save(self):
        with open("data.pickle", "wb") as fd:
            pickle.dump(self.deadlines, fd)

    def load(self):
        if os.path.exists("data.pickle"):
            with open("data.pickle", "rb") as fd:
                self.deadlines = pickle.load(fd)
                print("Loaded data about {} conferences".format(len(self.deadlines)))

    def set(self, name, when, uid):
        opts = self.deadlines.setdefault(name.upper(), [])
        confs = filter(lambda conf: when < conf.when, opts)
        if not confs: raise ValueError
        conf = min(confs, key=lambda x: x.when)
        conf.who.add(uid)
        self.save()

    def unset(self, name, when, uid):
        opts = self.deadlines.setdefault(name.upper(), [])
        confs = filter(lambda conf: when < conf.when, opts)
        if not confs: raise ValueError
        conf = min(confs, key=lambda x: x.when)
        conf.who.remove(uid)
        self.save()

    def add(self, name, when):
        self.deadlines.setdefault(name.upper(), []).append(Conference(when, set(), []))
        self.save()

    def modify(self, name, when):
        opts = self.deadlines.setdefault(name.upper(), [])
        confs = filter(lambda conf: when < conf.when, opts)
        if not confs: raise ValueError
        conf = min(confs, key=lambda x: x.when)

        i = self.deadlines[name.upper()].index(conf)
        self.deadlines[name.upper()][i] = Conference(when, conf.who, conf.announcements)
        self.save()

    def who(self, name, when):
        opts = self.deadlines.setdefault(name.upper(), [])
        confs = filter(lambda conf: when < conf.when, opts)
        if not confs: raise ValueError
        conf = min(confs, key=lambda x: x.when)
        return conf.who

    def when(self, name, when):
        opts = self.deadlines.setdefault(name.upper(), [])
        confs = filter(lambda conf: when < conf.when, opts)
        if not confs: raise ValueError
        conf = min(confs, key=lambda x: x.when)
        return conf.when

    def upcoming(self, when):
        out = []
        for name, opts in self.deadlines.items():
            confs = list(filter(lambda conf: when < conf.when, opts))
            if confs:
                out.append((name, min(confs, key=lambda x: x.when)))
        return sorted(out, key=lambda x: x[1].when)

    def all(self):
        out = []
        for name, opts in self.deadlines.items():
            for opt in opts:
                out.append((name, opt))
        return out

DATA = Deadlines()

def describe_who(who, conf):
    return ", ".join(["<@{}>".format(name) for name in who]) + " " + ("are" if len(who) > 1 else "is") + " submitting to " + conf.upper()

def parse_uid(user):
    assert user[0] == "<"
    assert user[-1] == ">"
    assert user[1] == "@"
    return user[2:-1].split("|")[0]

COMMANDS = []

def handle(uid, args):
    for pattern, opts, f in COMMANDS:
        if args.match(*pattern):
            print(" ".join(args.args), "(executing in {})".format(f.__name__))
            if opts["uid"]:
                return f(uid, *args.vars)
            else:
                return f(*args.vars)
    else:
        return Ephemeral("I couldn't understand that command\n\n" + help())

def command(*pattern, uid=False, public=False):
    def decorator(f):
        COMMANDS.append((pattern, { "uid": uid, "public": public }, f))
        return f
    return decorator

def lookup_tz(tz):
    try:
        return timedelta(seconds=int(tz)*3600)
    except ValueError:
        offset = {
            # US time zones are hour-aligned; server is in Pacific time zone
            "PT": (datetime.now() - datetime.utcnow()),
            "MT": (datetime.now() - datetime.utcnow()) + timedelta(seconds=3600),
            "CT": (datetime.now() - datetime.utcnow()) + timedelta(seconds=2*3600),
            "ET": (datetime.now() - datetime.utcnow()) + timedelta(seconds=3*3600),
            "AOE": timedelta(seconds=-12 * 3600),
        }[tz]
        return timedelta(seconds=round(offset.total_seconds()))

def new_announcements():
    now = datetime.now()
    announce_days = [28, 21, 14, 7, 6, 5, 4, 3, 2, 1, 0]
    out = []
    for name, conf in DATA.all():
        if conf.when < now: continue
        if conf.when > now + timedelta(days=max(announce_days)): continue
        announce = False
        for days in announce_days:
            if conf.when < now + timedelta(days=days) \
               and days not in conf.announcements:
                announce = True
                conf.announcements.append(days)
        if announce:
            out.append((name, conf))
    return out

def days(n):
    return ("1 day" if n == 1 else "{} days".format(n))

def make_announcements():
    now = datetime.now()
    for name, conf in new_announcements():
        print("Announcing", name, "on", conf.when)
        delta = round((conf.when - now) / timedelta(days=1))
        who = ", ".join(["<@{}>".format(uid) for uid in conf.who])
        if delta == 0:
            post("{} dealine! Congrats to everyone who submitted!".format(name))
        else:
            post("{} is in {}! Good luck {}".format(name, days(delta), who))

def start_announcement_thread():
    with DATA.lock():
        try:
            make_announcements()
        except Exception as e:
            import traceback
            traceback.print_exc()
        DATA.save()

    assert DATA.unlocked()

    FREQUENCY = 60 * 60
    timer = threading.Timer(FREQUENCY, start_announcement_thread)
    timer.start()
    return timer

class Commands:
    @command(["user"], "set", ["conf"])
    def set_user(user, conf):
        """Declare that someone is submitting to a conference"""
        uid2 = parse_uid(user)
        return Commands.set(uid2, conf)

    @command("set", ["conf"], uid=True)
    def set(uid, conf):
        """Declare that you are submitting to a conference"""
        try:
            DATA.set(conf, datetime.now(), uid)
            return Response("Good luck, <@{}>, on {}!".format(uid, conf.upper()))
        except ValueError:
            return Ephemeral("Conference {} does not yet exit. Please `/deadline add` it.".format(conf.upper()))

    @command(["user"], "unset", ["conf"])
    def unset_user(user, conf):
        """Declare that someone is no longer submitting to a conference"""
        uid = parse_uid(user)
        try:
            DATA.unset(conf, datetime.now(), uid)
            return Ephemeral("Sorry to hear that.")
        except ValueError:
            return Ephemeral("Conference {} does not yet exit. Please `/deadline add` it.".format(conf.upper()))

    @command("unset", ["conf"], uid=True)
    def unset(uid, conf):
        """Declare that you are no longer submitting to a conference"""
        try:
            DATA.unset(conf, datetime.now(), uid)
            return Ephemeral("Patience is bitter, but its fruit is sweet, <@{}>!".format(uid, conf.upper()))
        except ValueError:
            return Ephemeral("Conference {} does not yet exit. Please `/deadline add` it.".format(conf.upper()))

    @command("add", ["conf"], ["date"], ["time"], ["tz"])
    def add_tz(conf, date, time, tz):
        """Add a conference"""
        when = datetime.strptime(date + " " + time, "%Y-%m-%d %H:%M")
        offset = lookup_tz(tz) - lookup_tz("PT")
        when -= offset
        DATA.add(conf, when)
        return Ephemeral("Added {} on {} at {}".format(conf, when.strftime("%d %b"), when.strftime("%H:%M")))

    @command("add", ["conf"], ["date"], ["time"])
    def add(conf, date, time):
        """Add a conference"""
        return Commands.add_tz(conf, date, time, "PT")

    @command("modify", ["conf"], ["date"], ["time"], ["tz"])
    def modify_tz(conf, date, time, tz):
        """Change when a conference is"""
        when = datetime.strptime(date + " " + time, "%Y-%m-%d %H:%M")
        offset = lookup_tz(tz) - lookup_tz("PT")
        when -= offset
        DATA.modify(conf, when)
        return Ephemeral("Added {} on {}".format(conf, when.strftime("%d %b at %H:%M")))

    @command("modify", ["conf"], ["date"], ["time"])
    def modify(conf, date, time):
        """Change when a conference is"""
        return Commands.modify_tz(conf, date, time, "PT")

    @command("when", ["conf"])
    def when(conf):
        """When is a conference?"""
        when = DATA.when(conf, datetime.now())
        return Ephemeral("{} is on {}".format(conf.upper(), when.strftime("%d %b at %H:%M")))

    @command("who", ["conf"])
    def who(conf):
        """Who is submitting to a conference?"""
        who = DATA.who(conf, datetime.now())
        return Ephemeral(describe_who(who, conf))

    @command("upcoming", public=True)
    def upcoming():
        """List upcoming deadlines"""
        upcoming = DATA.upcoming(datetime.now())
        return Response("The following deadlines are coming up: " + ", ".join([
            "{} on {} ({})".format(
                name, conf.when.strftime("%d %b"), days(round((conf.when - datetime.now()) / timedelta(days=1)))
            ) for name, conf in upcoming
        ]))

    @command("announce", ["conf"], public=True)
    def announce(conf):
        """Announce who is submitting to a conference"""
        who = DATA.who(conf, datetime.now())
        return Response(describe_who(who, conf))

    @command("help")
    def help():
        return Ephemeral(help())

FORMATS = {
    "user": "@USER",
    "date": "YYYY-MM-DD",
    "time": "HH:MM",
}

def help():
    private = "I understand the following commands:\n\n"
    public = "Public announcement commands:\n\n"
    for pattern, opts, f in COMMANDS:
        if not f.__doc__: continue
        s = "• `/deadline "
        for val in pattern:
            if isinstance(val, list):
                s += FORMATS.get(val[0], val[0].upper())
            else:
                s += val
            s += " "
        s += "` — " + str(f.__doc__) + "\n"
        if opts["public"]:
            public += s
        else:
            private += s
    return private + "\n" + public

if __name__ == "__main__":
    print(help())
    import sys
    sys.exit()
    with DATA.lock():
        DATA.load()
    assert DATA.unlocked()

    try:
        announce_thread = start_announcement_thread()
        try:
            start_server()
        except KeyboardInterrupt:
            announce_thread.cancel()
    finally:
        with DATA.lock():
            DATA.save()
