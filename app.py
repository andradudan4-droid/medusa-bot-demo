"""
Salon demo platform
====================
One Flask app that serves a branded demo site + tailored booking assistant for
each salon you're pitching. Adding a new salon = add one entry to SALONS below.
Every demo lives at:  /demo/<slug>     e.g. /demo/medusa

Why one app instead of one-per-salon:
  - It stays awake with a single keep-warm pinger, so cold-outreach links open
    instantly instead of showing a 50-second blank page.
  - A new prospect is a few lines of config, not a fresh deploy.

Environment variables (set these in Render):
  GROQ_API_KEY     - your Groq key (the bot's brain)
  RESEND_API_KEY   - your Resend key (sends the booking emails)
  SELLER_EMAIL     - YOUR email; demo bookings land here so you see them working
"""

from flask import Flask, request, jsonify, render_template_string, session, Response, abort
import os
import re
import uuid
import threading
import requests
from groq import Groq

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")
app.config["SESSION_COOKIE_SAMESITE"] = "None"
app.config["SESSION_COOKIE_SECURE"] = True

client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
SELLER_EMAIL = os.environ.get("SELLER_EMAIL", "your-email@example.com")


# ===========================================================================
#  SALONS  -  add a new client by copying the medusa block and editing it
# ===========================================================================
SALONS = {
    "medusa": {
        "name": "Medusa Hairdressing",
        "tagline": "Award-winning boutique colour salon in Southsea",
        "blurb": (
            "A boutique colour destination salon on Fawcett Road, Southsea, with "
            "over 30 years' experience. Colour and cutting experts offering a "
            "premier bespoke colouring service - including our Celebrity Colour "
            "Menu - using Goldwell salon-exclusive products. Every guest gets a "
            "full 60-minute one-to-one visit, never a rushed 45."
        ),
        "phone": "02392 731 338",
        "phone_tel": "02392731338",
        "email": "info@medusahairdressing.com",
        "address": "72 Fawcett Road, Southsea, Hampshire PO4 0DN",
        "booking_url": "http://phore.st/vVopF",
        "instagram": "https://www.instagram.com/medusa_hairdressing/",
        "facebook": "https://en-gb.facebook.com/Medusahairsouthsea/",
        "logo": "https://medusahairdressing.com/wp-content/themes/medusahair/images/Hair-dressers-southsea.png",
        "hero_img": "https://medusahairdressing.com/wp-content/uploads/2024/03/Website-1.jpg",
        "gallery_img": "https://medusahairdressing.com/wp-content/uploads/2019/11/Medusa-hair.jpg",
        "map_embed": "https://www.google.com/maps/embed?pb=!1m18!1m12!1m3!1d4391.175758124669!2d-1.0778825261142475!3d50.79284530984688!2m3!1f0!2f0!3f0!3m2!1i1024!2i768!4f13.1!3m3!1m2!1s0x48745d97d5cd853d%3A0x4f6fbd229789a640!2sMedusa%20Hairdressing%20ltd!5e0!3m2!1sen!2suk!4v1692779374262!5m2!1sen!2suk",
        "awards": [
            "Finalist - British Hairdressing Business Awards 'Best Salon' (national, only 6 finalists)",
            "5 Stars from Phorest Premier Salon Services - 6 consecutive years (top 6% nationally)",
            "5 Stars from The Good Salon Guide",
            "Three Best Rated - Best Hairdressers in Portsmouth",
        ],
        "highlights": [
            ("60-minute one-to-one visits", "A full consultation every time - never a rushed appointment."),
            ("Bespoke colour experts", "30+ years of colour and cutting, plus our Celebrity Colour Menu."),
            ("Goldwell premium care", "Salon-exclusive products and expert advice to protect your colour."),
            ("Award-winning service", "Nationally recognised, consistently 5-star rated."),
        ],
        "palette": {
            "ink": "#1c1a19",       # near-black text / dark sections
            "bg": "#f6f2ec",        # warm ivory page background
            "surface": "#ffffff",   # cards
            "accent": "#9a7b4f",    # antique bronze - premium, salon-appropriate
            "accent_dark": "#7c6240",
            "muted": "#8a8178",     # captions / secondary text
        },
        # Real price list (guide prices) the bot is allowed to quote.
        "price_groups": [
            ("Cuts & Finishing", [
                ("Ladies cleanse, cut & finish", "£79"),
                ("Ladies restyle, cleanse, cut & finish", "£89"),
                ("Ladies cleanse & finish", "£50"),
                ("Mens cleanse, cut & finish", "£46"),
                ("Gender neutral cleanse, cut & finish", "from £46 (short) - £79 (long)"),
                ("Fringe trim", "£13"),
                ("Medusa full works", "£73"),
                ("Curls / waves (dry hair)", "from £42"),
                ("Prom hair with one trial", "from £78"),
            ]),
            ("Colour - Classic Menu", [
                ("Root retouch", "from £83"),
                ("Full head colour", "from £115"),
                ("Gloss colour", "from £83"),
                ("Full head highlights", "from £150"),
                ("Half head highlights", "from £126"),
                ("T-bar", "from £110"),
                ("Balayage", "from £160"),
                ("Creative Elumen / Elumen play", "from £121"),
                ("Pre-lightening roots (6-8 wks)", "from £120"),
                ("Pre-lightening roots (10+ wks)", "from £170"),
                ("Pre-lightening virgin full head", "from £200"),
            ]),
            ("Colour - Express Menu", [
                ("Express root service", "from £47"),
                ("Express toning service", "from £47"),
                ("Colour corrections", "consultation only"),
            ]),
            ("Treatments", [
                ("Medusa treatment (structure & shine)", "from £18"),
                ("Luxury scalp treatment incl. blow-dry", "£105"),
                ("Luxury scalp treatment incl. cleanse, cut & finish", "£130"),
            ]),
            ("Weddings & Occasions", [
                ("Bridal hair with one trial", "from £117"),
                ("Additional bridal trials", "from £42"),
                ("Bridesmaid hair", "from £82"),
                ("Mother of the bride", "from £73"),
            ]),
        ],
        "hours": [
            ("Monday", "9am - 6pm"),
            ("Tuesday", "9am - 6pm"),
            ("Wednesday", "9:30am - 2:30pm"),
            ("Thursday", "10am - 8pm"),
            ("Friday", "9am - 8pm"),
            ("Saturday", "9am - 5pm"),
            ("Sunday", "Closed"),
        ],
        # Demo bookings come to YOU (the seller). When the salon signs up,
        # change this to their own email, e.g. "info@medusahairdressing.com".
        "notify_email": SELLER_EMAIL,
        "policies": (
            "- New colour clients, and anyone who hasn't had colour with us in the "
            "last 6 months, need a free allergy alert (patch) test at least 48 hours "
            "before their colour appointment.\n"
            "- Every guest gets a full 60-minute one-to-one visit, including a "
            "consultation - we never rush your look (radical changes get 90 minutes).\n"
            "- A booking fee may apply when reserving an appointment; the salon "
            "confirms the details at the time of booking.\n"
            "- Colour corrections are by consultation only."
        ),
    },
}


# ===========================================================================
#  Contact detection  -  used to decide when a real booking has been captured
# ===========================================================================
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?:\+44\s?|0)\d(?:[\s-]?\d){8,11}")


def _customer_text(conversation):
    return " ".join(m["content"] for m in conversation if m.get("role") == "user")


def find_email(conversation):
    m = EMAIL_RE.search(_customer_text(conversation))
    return m.group(0) if m else None


def find_phone(conversation):
    for cand in PHONE_RE.findall(_customer_text(conversation)):
        digits = re.sub(r"\D", "", cand)
        if 10 <= len(digits) <= 13:
            return cand.strip()
    return None


def has_contact_info(conversation):
    return bool(find_email(conversation) or find_phone(conversation))


# ===========================================================================
#  Booking email
# ===========================================================================
def send_booking_email(salon, conversation):
    if not RESEND_API_KEY:
        print("RESEND_API_KEY not set, skipping booking email")
        return

    email = find_email(conversation) or "(none given)"
    phone = find_phone(conversation) or "(none given)"

    lines = []
    for msg in conversation:
        if msg["role"] == "user":
            lines.append(f"Customer: {msg['content']}")
        elif msg["role"] == "assistant":
            lines.append(f"Assistant: {msg['content']}")
    transcript = "\n\n".join(lines)

    summary = (
        f"NEW BOOKING ENQUIRY - {salon['name']}\n"
        "-----------------------------------\n"
        f"Phone: {phone}\n"
        f"Email: {email}\n"
        "-----------------------------------\n\n"
        "Full conversation:\n\n"
    )

    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
            json={
                # For demos this goes to your own inbox, so the resend.dev sender
                # is fine. Once a salon signs up and bookings go to THEIR email,
                # send from your own verified domain for inbox delivery.
                "from": "Salon Assistant <onboarding@resend.dev>",
                "to": [salon["notify_email"]],
                "subject": f"New booking enquiry - {salon['name']} - phone: {phone}",
                "text": summary + transcript,
            },
            timeout=10,
        )
        if resp.status_code >= 300:
            print(f"Resend error: {resp.status_code} {resp.text}")
    except Exception as e:
        print(f"Failed to send booking email: {e}")


# ===========================================================================
#  Per-salon system prompt
# ===========================================================================
def build_system_prompt(salon):
    price_lines = []
    for group, items in salon["price_groups"]:
        price_lines.append(f"\n{group}:")
        for name, price in items:
            price_lines.append(f"  - {name}: {price}")
    prices = "\n".join(price_lines)

    hours = "\n".join(f"  {day}: {time}" for day, time in salon["hours"])

    policies = salon.get("policies", "").strip()
    policies_block = (
        f"\nSalon policies (raise these naturally when relevant, don't just recite them):\n{policies}\n"
        if policies else ""
    )

    return f"""
You are the front-of-house assistant for "{salon['name']}", {salon['tagline']}.
You're chatting with someone on the salon's website. Think of yourself as a warm,
experienced salon receptionist who genuinely knows hair - friendly, reassuring and
helpful, never robotic or generic. Use the customer's name once you know it.

About the salon:
{salon['blurb']}
Address: {salon['address']}
Phone: {salon['phone']}

Opening hours:
{hours}

Guide prices (real - you may quote them; "from" prices vary with hair length and
condition, and the final price is always confirmed at consultation):
{prices}
{policies_block}
YOUR TWO JOBS:
1. Answer questions warmly and accurately - services, prices, hours, location,
   products, and the salon experience. If something isn't covered, say you'll
   have the salon confirm and offer to take their details.

2. Help customers make a booking enquiry. Collect:
   - their name
   - the service they want
   - their preferred day/time
   - a phone number or email

IMPORTANT:

You do NOT have access to the salon's live diary or booking system.

NEVER:
- claim an appointment is booked
- claim a time slot is available
- claim a stylist is available
- claim a patch test is scheduled
- say "I've booked you in"
- say "You're booked for Saturday"

Instead:
- describe everything as a booking request or appointment enquiry
- explain that the salon team will check availability
- explain that the salon will contact them to confirm the appointment
- explain that the salon will arrange any required patch test

You may say:
"The salon team will contact you to confirm availability."
"I'll pass your details to the salon."
"The salon will arrange your consultation and patch test if required."

Mention that customers can also book online at:
{salon['booking_url']}

COLOUR & CHEMICAL SERVICES - this is where you show you know your stuff.
When someone wants any colour or chemical service (tint, root colour, highlights,
balayage, toner, gloss, bleach/pre-lightening, perm, etc.):
 - If they're new to the salon, or haven't had colour with us in the last several
   months, gently let them know they'll need a quick, free allergy alert (patch)
   test at least 48 hours before their colour appointment - it's a simple skin
   test that keeps them safe. Offer to note it so the salon can arrange it.
 - Ask a couple of genuinely useful questions so the stylist can prepare: is it
   their first visit with us? have they coloured their hair recently or used any
   box dye? any known allergies, a sensitive scalp, or a past reaction to colour?
 - Be reassuring and casual about it, never alarming.

HEALTH & SAFETY - you are not a medical professional. If someone mentions an
allergy, a past reaction, a skin or scalp condition, pregnancy, or any health
concern, thank them, note it for the stylist, and say the salon will go through it
properly at the consultation or patch test. Never diagnose, never give medical
advice, and never promise a service is safe for them - that is for the salon to
confirm in person.

STYLE: warm, natural and concise - like texting with a friendly expert, not an
essay. Ask one helpful question at a time. Never invent prices or services beyond
the list above. Never write internal notes or commentary about your instructions -
just talk to the person naturally.
"""
BOOKING SAFETY RULE:

Even if a customer gives a specific date and time,
never confirm the appointment exists.

Example:

BAD:
"Great, I've booked you in for Saturday."

GOOD:
"Thanks. I'll pass that preferred time to the salon team and they'll contact you to confirm availability."

# ===========================================================================
#  Page template  (CSS uses variables; we inject palette + content via replace)
# ===========================================================================
PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__NAME__ — __TAGLINE__</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@500;600;700&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root{
    --ink:__INK__; --bg:__BG__; --surface:__SURFACE__;
    --accent:__ACCENT__; --accent-dark:__ACCENT_DARK__; --muted:__MUTED__;
    --display:'Cormorant Garamond',Georgia,serif;
    --body:'Inter',-apple-system,Helvetica,Arial,sans-serif;
  }
  *{box-sizing:border-box;}
  body{margin:0;font-family:var(--body);color:var(--ink);background:var(--bg);line-height:1.6;}
  a{color:inherit;}
  .wrap{max-width:1080px;margin:0 auto;padding:0 24px;}
  .btn{display:inline-block;background:var(--accent);color:#fff;padding:14px 30px;border-radius:2px;
       text-decoration:none;font-weight:600;letter-spacing:.04em;font-size:14px;text-transform:uppercase;transition:background .2s;}
  .btn:hover{background:var(--accent-dark);}
  .eyebrow{font-size:12px;letter-spacing:.28em;text-transform:uppercase;color:var(--accent);font-weight:600;}

  /* nav */
  nav{display:flex;align-items:center;justify-content:space-between;padding:18px 24px;max-width:1080px;margin:0 auto;flex-wrap:wrap;gap:12px;}
  nav .brand{display:flex;align-items:center;gap:12px;font-family:var(--display);font-size:24px;font-weight:700;letter-spacing:.02em;}
  nav .brand img{height:38px;width:auto;}
  nav .links a{margin-left:26px;text-decoration:none;font-size:13px;letter-spacing:.12em;text-transform:uppercase;color:var(--ink);opacity:.75;}
  nav .links a:hover{opacity:1;color:var(--accent);}

  /* hero */
  .hero{position:relative;min-height:74vh;display:flex;align-items:center;justify-content:center;text-align:center;color:#fff;
        background:linear-gradient(rgba(20,17,15,.55),rgba(20,17,15,.65)),url('__HERO_IMG__') center/cover;}
  .hero .inner{max-width:760px;padding:40px 24px;}
  .hero h1{font-family:var(--display);font-weight:700;font-size:clamp(40px,7vw,76px);line-height:1.04;margin:14px 0 18px;}
  .hero p{font-size:18px;opacity:.92;max-width:560px;margin:0 auto 30px;}
  .hero .eyebrow{color:#e8d8c2;}

  /* sections */
  section{padding:84px 0;}
  .section-head{max-width:640px;margin:0 auto 48px;text-align:center;}
  .section-head h2{font-family:var(--display);font-size:clamp(30px,4.5vw,46px);font-weight:600;margin:10px 0 0;}
  .section-head p{color:var(--muted);margin-top:10px;}

  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:24px;}
  .card{background:var(--surface);padding:30px 26px;border-radius:3px;box-shadow:0 1px 0 rgba(0,0,0,.04);border-top:2px solid var(--accent);}
  .card h3{font-family:var(--display);font-size:23px;font-weight:600;margin:0 0 8px;}
  .card p{margin:0;color:var(--muted);font-size:15px;}

  /* prices */
  .prices{background:var(--ink);color:#f3ece2;}
  .prices .section-head h2{color:#fff;}
  .prices .section-head p{color:#bcae9c;}
  .price-cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:40px 56px;}
  .price-group h3{font-family:var(--display);font-size:24px;color:var(--accent);margin:0 0 16px;font-weight:600;
                  border-bottom:1px solid rgba(255,255,255,.14);padding-bottom:10px;}
  .price-row{display:flex;justify-content:space-between;gap:18px;padding:7px 0;font-size:15px;}
  .price-row .nm{opacity:.92;}
  .price-row .pr{color:#e8d8c2;white-space:nowrap;font-variant-numeric:tabular-nums;}
  .price-note{text-align:center;color:#bcae9c;font-size:13px;margin-top:40px;}

  /* visit / info */
  .visit{display:grid;grid-template-columns:1.1fr .9fr;gap:48px;align-items:center;}
  .visit img{width:100%;border-radius:3px;display:block;}
  .hours-table{width:100%;border-collapse:collapse;margin-top:8px;}
  .hours-table td{padding:9px 0;border-bottom:1px solid rgba(0,0,0,.08);font-size:15px;}
  .hours-table td:last-child{text-align:right;color:var(--muted);}
  .contact-line{margin:6px 0;font-size:15px;}
  .map{width:100%;height:300px;border:0;border-radius:3px;margin-top:28px;filter:grayscale(.2);}

  footer{background:var(--ink);color:#bcae9c;text-align:center;padding:44px 24px;font-size:14px;}
  footer .demo-note{margin-top:14px;font-size:12px;opacity:.6;}

  @media(max-width:760px){
    .visit{grid-template-columns:1fr;}
    nav .links{display:none;}
    section{padding:60px 0;}
  }
</style>
</head>
<body>

<nav>
  <div class="brand"><img src="__LOGO__" alt="__NAME__ logo">__NAME__</div>
  <div class="links">
    <a href="#why">Salon</a>
    <a href="#prices">Prices</a>
    <a href="#visit">Visit</a>
    <a href="__BOOKING_URL__" target="_blank">Book</a>
  </div>
</nav>

<header class="hero">
  <div class="inner">
    <div class="eyebrow">__TAGLINE__</div>
    <h1>__NAME__</h1>
    <p>__BLURB_SHORT__</p>
    <a class="btn" href="__BOOKING_URL__" target="_blank">Book your visit</a>
  </div>
</header>

<section id="why">
  <div class="wrap">
    <div class="section-head"><div class="eyebrow">Why __FIRSTNAME__</div><h2>The salon experience</h2>
      <p>__BLURB__</p></div>
    <div class="grid">__HIGHLIGHTS__</div>
  </div>
</section>

<section id="prices" class="prices">
  <div class="wrap">
    <div class="section-head"><div class="eyebrow">Price list</div><h2>Services &amp; prices</h2>
      <p>Guide prices — your final price is confirmed at your free consultation.</p></div>
    <div class="price-cols">__PRICES__</div>
    <p class="price-note">Ask the assistant in the corner about any service, or book online anytime.</p>
  </div>
</section>

<section id="visit">
  <div class="wrap">
    <div class="visit">
      <div>
        <img src="__GALLERY_IMG__" alt="Inside __NAME__">
      </div>
      <div>
        <div class="eyebrow">Find us</div>
        <h2 style="font-family:var(--display);font-size:34px;font-weight:600;margin:8px 0 18px;">Visit __FIRSTNAME__</h2>
        <p class="contact-line">📍 __ADDRESS__</p>
        <p class="contact-line">📞 <a href="tel:__PHONE_TEL__">__PHONE__</a></p>
        <p class="contact-line">✉️ <a href="mailto:__EMAIL__">__EMAIL__</a></p>
        <table class="hours-table">__HOURS__</table>
      </div>
    </div>
    <iframe class="map" src="__MAP_EMBED__" loading="lazy" referrerpolicy="no-referrer-when-downgrade" title="Map"></iframe>
  </div>
</section>

<footer>
  __NAME__ · __ADDRESS__<br>
  <a href="__INSTAGRAM__" target="_blank">Instagram</a> &nbsp;·&nbsp; <a href="__FACEBOOK__" target="_blank">Facebook</a>
  <div class="demo-note">Demo site with AI booking assistant · built for __NAME__</div>
</footer>

<script src="/demo/__SLUG__/widget.js"></script>
</body>
</html>
"""


def render_salon_page(slug, salon):
    p = salon["palette"]
    first = salon["name"].split()[0]

    highlights = "".join(
        f'<div class="card"><h3>{t}</h3><p>{d}</p></div>'
        for t, d in salon["highlights"]
    )

    price_html = ""
    for group, items in salon["price_groups"]:
        rows = "".join(
            f'<div class="price-row"><span class="nm">{n}</span><span class="pr">{pr}</span></div>'
            for n, pr in items
        )
        price_html += f'<div class="price-group"><h3>{group}</h3>{rows}</div>'

    hours = "".join(
        f"<tr><td>{day}</td><td>{time}</td></tr>" for day, time in salon["hours"]
    )

    short_blurb = salon["blurb"].split(". ")[0] + "."

    replacements = {
        "__INK__": p["ink"], "__BG__": p["bg"], "__SURFACE__": p["surface"],
        "__ACCENT__": p["accent"], "__ACCENT_DARK__": p["accent_dark"], "__MUTED__": p["muted"],
        "__NAME__": salon["name"], "__FIRSTNAME__": first,
        "__TAGLINE__": salon["tagline"], "__BLURB__": salon["blurb"], "__BLURB_SHORT__": short_blurb,
        "__LOGO__": salon["logo"], "__HERO_IMG__": salon["hero_img"], "__GALLERY_IMG__": salon["gallery_img"],
        "__PHONE__": salon["phone"], "__PHONE_TEL__": salon["phone_tel"],
        "__EMAIL__": salon["email"], "__ADDRESS__": salon["address"],
        "__BOOKING_URL__": salon["booking_url"], "__MAP_EMBED__": salon["map_embed"],
        "__INSTAGRAM__": salon["instagram"], "__FACEBOOK__": salon["facebook"],
        "__HIGHLIGHTS__": highlights, "__PRICES__": price_html, "__HOURS__": hours,
        "__SLUG__": slug,
    }
    html = PAGE
    for k, v in replacements.items():
        html = html.replace(k, v)
    return html


# ===========================================================================
#  Chat widget (bubble + iframe) - injected onto the demo page
# ===========================================================================
WIDGET_JS = """(function(){
  var slug="__SLUG__", accent="__ACCENT__", ink="__INK__";
  var bubble=document.createElement('div');
  bubble.innerHTML='<svg width="30" height="30" viewBox="0 0 24 24" fill="none"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  bubble.style.cssText='position:fixed;bottom:22px;right:22px;width:64px;height:64px;border-radius:50%;background:'+accent+';display:flex;align-items:center;justify-content:center;cursor:pointer;box-shadow:0 6px 20px rgba(0,0,0,.28);z-index:999999;transition:transform .15s;';
  bubble.onmouseenter=function(){bubble.style.transform='scale(1.06)';};
  bubble.onmouseleave=function(){bubble.style.transform='scale(1)';};
  var frame=document.createElement('iframe');
  frame.src='/demo/'+slug+'/widget-frame';
  function style(){var m=window.innerWidth<=600;
    frame.style.cssText=m
      ?'position:fixed;inset:0;width:100%;height:100%;border:none;display:none;z-index:999999;'
      :'position:fixed;bottom:100px;right:22px;width:390px;height:560px;border:none;border-radius:16px;box-shadow:0 16px 50px rgba(0,0,0,.3);display:none;z-index:999999;';}
  style();window.addEventListener('resize',function(){var o=frame.style.display==='block';style();if(o)frame.style.display='block';});
  var open=false;
  bubble.onclick=function(){open=!open;frame.style.display=open?'block':'none';};
  window.addEventListener('message',function(e){if(e.data==='close-chat'){open=false;frame.style.display='none';}});
  document.body.appendChild(bubble);document.body.appendChild(frame);
})();
"""

WIDGET_FRAME = """<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@600&family=Inter:wght@400;500&display=swap" rel="stylesheet">
<style>
 :root{--accent:__ACCENT__;--ink:__INK__;}
 *{box-sizing:border-box;}
 body{margin:0;font-family:'Inter',-apple-system,Arial,sans-serif;}
 #win{display:flex;flex-direction:column;height:100vh;background:#fff;border-radius:16px;overflow:hidden;}
 #head{background:var(--ink);color:#fff;padding:18px 20px;display:flex;align-items:center;justify-content:space-between;}
 #head .t{font-family:'Cormorant Garamond',serif;font-size:21px;font-weight:600;}
 #head .s{font-size:12px;opacity:.7;}
 #head .x{cursor:pointer;font-size:22px;opacity:.8;line-height:1;}
 #box{flex:1;padding:18px;overflow-y:auto;background:#faf7f2;}
 .m{margin:9px 0;padding:11px 15px;border-radius:14px;max-width:84%;font-size:15px;line-height:1.45;}
 .u{background:var(--ink);color:#fff;margin-left:auto;}
 .b{background:#eee7dd;color:#2a2622;}
 #row{display:flex;gap:8px;padding:12px;border-top:1px solid #eee;background:#fff;}
 #in{flex:1;padding:11px 15px;border:1px solid #ddd;border-radius:22px;font-size:15px;outline:none;}
 #in:focus{border-color:var(--accent);}
 #send{border:none;background:var(--accent);color:#fff;width:44px;height:44px;border-radius:50%;cursor:pointer;flex-shrink:0;font-size:18px;}
</style></head><body>
<div id="win">
  <div id="head"><div><div class="t">__NAME__</div><div class="s">Ask about services, prices or booking</div></div>
    <div class="x" onclick="parent.postMessage('close-chat','*')">&times;</div></div>
  <div id="box"></div>
  <div id="row"><input id="in" placeholder="Type a message…" onkeypress="if(event.key==='Enter')go()">
    <button id="send" onclick="go()">➤</button></div>
</div>
<script>
 var slug="__SLUG__";
 add("Hi, welcome to __NAME__! I can help with services, prices and opening hours, or get you booked in. What can I help you with today?", 'b');
 function add(t,s){var b=document.getElementById('box');var d=document.createElement('div');d.className='m '+s;d.textContent=t;b.appendChild(d);b.scrollTop=b.scrollHeight;}
 async function go(){var i=document.getElementById('in');var msg=i.value.trim();if(!msg)return;add(msg,'u');i.value='';
   try{var r=await fetch('/demo/'+slug+'/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:msg}),credentials:'same-origin'});
   var d=await r.json();add(d.reply,'b');}catch(e){add("Sorry, something went wrong - please call us instead.",'b');}}
</script></body></html>
"""


def inject(tpl, salon, slug):
    p = salon["palette"]
    return (tpl.replace("__SLUG__", slug)
               .replace("__ACCENT__", p["accent"])
               .replace("__INK__", p["ink"])
               .replace("__NAME__", salon["name"]))


# ===========================================================================
#  Conversation store + routes
# ===========================================================================
conversations = {}   # (slug, session_id) -> messages
notified = set()      # (slug, session_id)


def ensure_session():
    if "sid" not in session:
        session["sid"] = str(uuid.uuid4())
    return session["sid"]


@app.route("/")
def index():
    # No public landing page - we don't want the list of salons we're pitching
    # to be visible to anyone who hits the bare domain. Prospects only ever get
    # their own direct /demo/<slug> link, so the root just returns a neutral 404.
    abort(404)


@app.route("/demo/<slug>")
def demo(slug):
    salon = SALONS.get(slug)
    if not salon:
        abort(404)
    ensure_session()
    return render_template_string(render_salon_page(slug, salon))


@app.route("/demo/<slug>/widget.js")
def widget_js(slug):
    salon = SALONS.get(slug)
    if not salon:
        abort(404)
    return Response(inject(WIDGET_JS, salon, slug), mimetype="application/javascript")


@app.route("/demo/<slug>/widget-frame")
def widget_frame(slug):
    salon = SALONS.get(slug)
    if not salon:
        abort(404)
    ensure_session()
    return render_template_string(inject(WIDGET_FRAME, salon, slug))


@app.route("/demo/<slug>/chat", methods=["POST"])
def chat(slug):
    salon = SALONS.get(slug)
    if not salon:
        abort(404)
    sid = ensure_session()
    key = (slug, sid)

    if key not in conversations:
        conversations[key] = [{"role": "system", "content": build_system_prompt(salon)}]
    convo = conversations[key]

    convo.append({"role": "user", "content": request.json.get("message", "")})

    resp = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=convo,
        max_tokens=320,
    )
    reply = resp.choices[0].message.content
    convo.append({"role": "assistant", "content": reply})

    # Email the salon (you, during demos) the moment we have a real contact.
    if key not in notified and has_contact_info(convo):
        notified.add(key)
        threading.Thread(
            target=send_booking_email, args=(salon, list(convo)), daemon=True
        ).start()

    return jsonify({"reply": reply})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port)
