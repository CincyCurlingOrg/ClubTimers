#!/usr/bin/env python3
import os, sys, time, random, traceback
from datetime import datetime, timedelta

# ------------ Config ------------
LOGO_PATH = "/home/admin/logo.png"
SPLASH_WAIT_FOR_BUTTON = True

PRESETS = {
    "Standard 4s": 100 * 60,   # 1h40m
    "Bonspiel"   : 100 * 60,   # 1h40m
    "Doubles"    :  90 * 60,   # 1h30m
}
THRESHOLDS = {
    "Standard 4s": {"yellow": 15*60, "red": 0},
    "Bonspiel"   : {"yellow": 15*60, "red": 0},
    "Doubles"    : {"yellow": None,  "red": 5*60},
}

COIN_RESULT_SECONDS = 5          # total coin screen time
COIN_REVEAL_DELAY_SECONDS = 3    # wait this long before showing HEADS/TAILS
POST_ZERO_TO_SPLASH_MIN = 10     # 10 min after 0 → splash

# ------------ Fonts & Colors ------------
FONT_LARGE = 340      # timer digits
FONT_MED   = 160
FONT_SMALL = 72
FONT_SPLASH_HINT = 44
FONT_COIN_HINT = 56   # "Press START to begin" on coin result

WHITE=(255,255,255); BLACK=(0,0,0)
YELLOW=(255,215,0); RED=(220,20,60)
GREY=(35,35,35)
DARKBG=(10,10,10)

# ------------ GPIO Pins (BCM) ------------
USE_GPIO=True
PIN_MODE, PIN_COIN, PIN_START, PIN_STOP, PIN_RESET = 5,6,17,27,22
DEBOUNCE_S=0.05
RESET_HOLD_SECONDS=5.0

# ------------ Logging ------------
DEBUG_LOG="/home/admin/timer_debug.log"
def log(msg):
    try:
        line=f"[{time.strftime('%H:%M:%S')}] {msg}"
        print(line,flush=True)
        with open(DEBUG_LOG,"a") as f:f.write(line+"\n")
    except Exception:pass

# ------------ Imports ------------
import pygame
pygame.display.init(); pygame.font.init()
if USE_GPIO:
    try:
        from gpiozero import Button
    except Exception as e:
        log(f"GPIO unavailable: {e}"); USE_GPIO=False

# ------------ Helpers ------------
_font_cache={}
def get_font(size,bold=True):
    k=(size,bold)
    if k not in _font_cache:
        _font_cache[k]=pygame.font.SysFont("DejaVu Sans",size,bold=bold)
    return _font_cache[k]

def draw_text(surface,text,size,color=WHITE,pos=None,center=True):
    f=get_font(size,True)
    t=f.render(text,True,color)
    if center:
        sw,sh=surface.get_size()
        x=(sw-t.get_width())//2; y=(sh-t.get_height())//2
        surface.blit(t,(x,y))
    else:
        surface.blit(t,pos)

def format_time(sec):
    sec=max(0,int(sec)); m,s=divmod(sec,60)
    return f"{m:02d}:{s:02d}"

def get_border_color(mode,secs_left):
    th=THRESHOLDS.get(mode,{})
    y,r=th.get("yellow"),th.get("red")
    if r is not None and secs_left<=r: return RED
    if secs_left<=0: return RED
    if y is not None and secs_left<=y: return YELLOW
    return None

def load_image_safe(path):
    try:
        if not os.path.isfile(path): return None
        return pygame.image.load(path).convert()
    except Exception: return None

def scale_to_screen(img,screen_size):
    if img is None: return None
    sw,sh=screen_size; iw,ih=img.get_width(),img.get_height()
    scale=min(sw/iw,sh/ih)
    new=(max(1,int(iw*scale)),max(1,int(ih*scale)))
    return pygame.transform.scale(img,new)

# ------------ Main App ------------
class CurlingTimerApp:
    def __init__(self):
        self.screen=pygame.display.set_mode((0,0),pygame.FULLSCREEN)
        self.screen_size=self.screen.get_size()
        self.clock=pygame.time.Clock()
        pygame.mouse.set_visible(False)
        self.state="SPLASH"

        self.mode_names=list(PRESETS.keys())
        self.mode_index=0
        self.current_mode=self.mode_names[0]
        self.original_time=PRESETS[self.current_mode]
        self.time_left=self.original_time
        self.running=False
        self.last_tick=time.time()
        self.coin_flip_result=None
        self.coin_flip_pick_at=None
        self.coin_flip_end_at=None
        self.zero_reached_at=None
        self._last_second=None
        self.logo_img=scale_to_screen(load_image_safe(LOGO_PATH),self.screen_size)

        if USE_GPIO:
            try:
                self.mode_btn=Button(PIN_MODE,pull_up=True,bounce_time=DEBOUNCE_S)
                self.coin_btn=Button(PIN_COIN,pull_up=True,bounce_time=DEBOUNCE_S)
                self.start_btn=Button(PIN_START,pull_up=True,bounce_time=DEBOUNCE_S)
                self.stop_btn=Button(PIN_STOP,pull_up=True,bounce_time=DEBOUNCE_S)
                self.reset_btn=Button(PIN_RESET,pull_up=True,bounce_time=DEBOUNCE_S,
                                      hold_time=RESET_HOLD_SECONDS,hold_repeat=False)
                self.mode_btn.when_pressed=lambda:self.on_mode("GPIO")
                self.coin_btn.when_pressed=lambda:self.on_coin("GPIO")
                self.start_btn.when_pressed=lambda:self.on_start("GPIO")
                self.stop_btn.when_pressed=lambda:self.on_stop("GPIO")
                self.reset_btn.when_held=lambda:self.on_reset_held("GPIO")
            except Exception as e: log(f"GPIO init fail {e}")

    # ---------- Button Handlers ----------
    def on_mode(self,src=""):
        if self.state=="SPLASH": self.state="MAIN"; return
        if self.running: return
        self.mode_index=(self.mode_index+1)%len(self.mode_names)
        self.current_mode=self.mode_names[self.mode_index]
        self.original_time=PRESETS[self.current_mode]
        self.time_left=self.original_time
        self._last_second=None; self.zero_reached_at=None

    def on_coin(self,src=""):
        # 🔒 Disable coin flip while timer is running
        if self.running:
            log("Coin flip ignored: timer running")
            return
        if self.state=="COIN": return
        if self.state=="SPLASH": self.state="MAIN"; return
        self.coin_flip_result=None
        now=datetime.now()
        # wait longer before revealing result
        self.coin_flip_pick_at=now+timedelta(seconds=COIN_REVEAL_DELAY_SECONDS)
        # still auto-return after 5 seconds total
        self.coin_flip_end_at=now+timedelta(seconds=COIN_RESULT_SECONDS)
        self.state="COIN"

    def on_start(self,src=""):
        if self.state=="SPLASH": self.state="MAIN"; return
        if self.time_left>0:
            self.running=True; self.last_tick=time.time()

    def on_stop(self,src=""): self.running=False
    def on_reset_held(self,src=""): self.on_reset_anywhere(src)
    def on_reset_anywhere(self,src=""):
        self.time_left=self.original_time; self.running=False
        self.coin_flip_result=None; self.zero_reached_at=None
        self._last_second=None; self.state="MAIN"

    # ---------- Logic ----------
    def update_timer(self):
        if self.running and self.time_left>0:
            now=time.time(); dt=now-self.last_tick; self.last_tick=now
            self.time_left=max(0,self.time_left-dt)
            if self.time_left<=0 and self.zero_reached_at is None:
                self.zero_reached_at=datetime.now()
        if self.zero_reached_at:
            if (datetime.now()-self.zero_reached_at).total_seconds()>=POST_ZERO_TO_SPLASH_MIN*60:
                self.state="SPLASH"; self.running=False

    def update_coin(self):
        if self.state!="COIN": return
        now=datetime.now()
        if self.coin_flip_result is None and now>=self.coin_flip_pick_at:
            self.coin_flip_result=random.choice(["HEADS","TAILS"])
        if now>=self.coin_flip_end_at:
            self.state="MAIN"; self.coin_flip_pick_at=None; self.coin_flip_end_at=None

    # ---------- Rendering ----------
    def render_splash(self):
        self.screen.fill(BLACK)
        if self.logo_img:
            iw,ih=self.logo_img.get_size()
            self.screen.blit(self.logo_img,((self.screen_size[0]-iw)//2,(self.screen_size[1]-ih)//2))
        # top-right hint
        f=get_font(FONT_SPLASH_HINT,False)
        text="Press any button to begin"
        t=f.render(text,True,WHITE)
        self.screen.blit(t,(self.screen_size[0]-t.get_width()-40,40))
        pygame.display.flip()

    def render_main(self):
        cur=int(self.time_left)
        if cur==self._last_second: return
        self._last_second=cur
        self.screen.fill(GREY)
        border=get_border_color(self.current_mode,self.time_left)
        if border: pygame.draw.rect(self.screen,border,self.screen.get_rect(),14)
        # Mode name top center
        f_mode=get_font(FONT_SMALL,True)
        mode_t=f_mode.render(f"Mode: {self.current_mode}",True,WHITE)
        self.screen.blit(mode_t,((self.screen_size[0]-mode_t.get_width())//2,40))
        # Big yellow timer
        f_time=get_font(FONT_LARGE,True)
        time_t=f_time.render(format_time(self.time_left),True,YELLOW)
        self.screen.blit(time_t,((self.screen_size[0]-time_t.get_width())//2,
                                 (self.screen_size[1]-time_t.get_height())//2))
        pygame.display.flip()

    def render_coin(self):
        self.screen.fill(DARKBG)
        if self.coin_flip_result is None:
            draw_text(self.screen,"Flipping...",FONT_MED,WHITE,center=True)
        else:
            draw_text(self.screen,self.coin_flip_result,FONT_LARGE,WHITE,center=True)
            # Bottom hint
            f=get_font(FONT_COIN_HINT,True)
            hint=f.render("Press START to begin",True,WHITE)
            x=(self.screen_size[0]-hint.get_width())//2
            y=self.screen_size[1]-hint.get_height()-40
            self.screen.blit(hint,(x,y))
        pygame.display.flip()

    # ---------- Main Loop ----------
    def run(self):
        while True:
            for e in pygame.event.get():
                if e.type==pygame.QUIT: pygame.quit(); sys.exit(0)
                elif e.type==pygame.KEYDOWN:
                    k=e.key
                    if k==pygame.K_ESCAPE: pygame.quit(); sys.exit(0)
                    if self.state=="SPLASH": self.state="MAIN"; continue
                    if k==pygame.K_m: self.on_mode("KEY")
                    elif k==pygame.K_c: self.on_coin("KEY")
                    elif k in [pygame.K_s,pygame.K_SPACE]: self.on_start("KEY")
                    elif k==pygame.K_x: self.on_stop("KEY")
                    elif k==pygame.K_r: self.on_reset_anywhere("KEY")

            if self.state=="SPLASH":
                self.render_splash()
            elif self.state=="COIN":
                self.update_coin()
                if self.state!="COIN": continue
                self.render_coin()
            else:
                self.update_timer()
                self.render_main()
            self.clock.tick(30)

# ---- Entrypoint ----
if __name__=="__main__":
    try:
        CurlingTimerApp().run()
    except Exception as e:
        log(f"FATAL: {e}"); log(traceback.format_exc()); sys.exit(1)
