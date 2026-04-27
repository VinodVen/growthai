# Revvio — Internal Technical Documentation

**Version:** 1.0  
**Last Updated:** April 2026  
**Stack:** Python / Flask / PostgreSQL / Render  
**Live URL:** https://revvio.ai

---

## Table of Contents

1. [App Overview](#1-app-overview)
2. [Architecture](#2-architecture)
3. [Database Models](#3-database-models)
4. [Environment Variables](#4-environment-variables)
5. [Feature Modules](#5-feature-modules)
6. [All Routes / API Endpoints](#6-all-routes--api-endpoints)
7. [User Flows](#7-user-flows)
8. [Plans & Permissions](#8-plans--permissions)
9. [Third-Party Integrations](#9-third-party-integrations)
10. [Deployment](#10-deployment)
11. [Admin Panel](#11-admin-panel)

---

## 1. App Overview

Revvio is a multi-tenant SaaS platform that helps businesses grow their customer base through:
- AI-generated marketing campaigns (Email, SMS, WhatsApp)
- Automated customer journeys
- QR code check-in with welcome coupons
- Public business landing page with product catalog
- AI-powered social media post generation
- Revenue ROI tracking
- Square POS integration
- White-label branding per client

Each registered business gets their own isolated account. All customer data, campaigns, and settings are scoped by `business_id`.

---

## 2. Architecture

```
┌─────────────────────────────────────────────────┐
│                   Client Browser                │
│         (HTML templates served by Flask)        │
└────────────────────┬────────────────────────────┘
                     │ HTTPS
┌────────────────────▼────────────────────────────┐
│              Render.com (PaaS)                  │
│  ┌─────────────────────────────────────────┐    │
│  │   Gunicorn (1 worker, timeout 120s)     │    │
│  │   Flask App  ──  main.py                │    │
│  │   SQLAlchemy ORM                        │    │
│  └──────────────┬──────────────────────────┘    │
│                 │                               │
│  ┌──────────────▼──────────────────────────┐    │
│  │         PostgreSQL (Render DB)          │    │
│  └─────────────────────────────────────────┘    │
└─────────────────────────────────────────────────┘

External APIs:
├── OpenAI (gpt-4o-mini)     → AI content generation
├── Twilio                   → SMS / WhatsApp
├── Resend                   → Email delivery
├── Stripe                   → Payments / subscriptions
├── Meta Graph API v18.0     → Facebook / Instagram posting
├── Square API               → POS payment sync
└── Google OAuth2            → Social login
```

**Key files:**
| File | Purpose |
|---|---|
| `main.py` | Entire backend — models, routes, business logic |
| `templates/` | Jinja2 HTML templates (one per page) |
| `static/` | CSS, JS, images, uploaded logos/product photos |
| `Procfile` | Gunicorn startup command for Render |
| `requirements.txt` | Python dependencies |

---

## 3. Database Models

### Business
Primary account — one per registered business.

| Column | Type | Description |
|---|---|---|
| id | Integer PK | Auto-increment |
| business_name | String(200) | Business display name |
| owner_name | String(200) | Owner full name |
| email | String(200) UNIQUE | Login email |
| password | String(200) | Bcrypt hashed |
| plan | String(50) | free / pro / enterprise |
| stripe_customer_id | String(200) | Stripe billing ID |
| trial_ends_at | DateTime | 30-day trial expiry |
| address | String(300) | Business address |
| phone | String(50) | Business phone |
| website | String(200) | Business website |
| slug | String(200) UNIQUE | URL slug (e.g. grill-4) |
| brand_name | String(200) | White-label name |
| brand_color | String(20) | Hex color (default #7c3aed) |
| logo_url | String(500) | Uploaded logo path |
| custom_subdomain | String(100) | e.g. "mybrand" → mybrand.revvio.ai |
| disabled_features | Text | Comma-separated disabled features |
| onboarding_done | Boolean | Dismiss onboarding checklist |
| welcome_offer_enabled | Boolean | Enable join coupon |
| welcome_offer_amount | String(50) | e.g. "$5" or "₹100" |
| welcome_offer_text | String(200) | e.g. "off your first visit" |
| welcome_offer_expiry_days | Integer | Days coupon is valid |
| referral_code | String(20) UNIQUE | Shareable referral code |
| referral_count | Integer | How many signups referred |
| last_login | DateTime | Last login timestamp |
| created_at | DateTime | Account creation date |

### Customer
One per customer per business (isolated by business_id).

| Column | Type | Description |
|---|---|---|
| id | Integer PK | |
| business_id | FK → businesses | Owner business |
| first_name | String(100) | |
| last_name | String(100) | |
| email | String(200) | |
| phone | String(50) | |
| dob | String(20) | Date of birth YYYY-MM-DD |
| source | String(50) | signup / qr_checkin / import / manual |
| unsubscribed | Boolean | Email unsubscribed |
| sms_opted_in | Boolean | SMS consent |
| whatsapp_phone | String(50) | WhatsApp number |
| whatsapp_opted_in | Boolean | WhatsApp consent |
| checkin_token | String(64) | Unique QR token per customer |
| created_at | DateTime | |

### Campaign
Individual message sent to one customer.

| Column | Type | Description |
|---|---|---|
| id | Integer PK | |
| business_id | FK | |
| customer_email | String(200) | Target email |
| campaign_type | String(50) | birthday / winback / weekly / etc. |
| subject | String(300) | Email subject |
| message | Text | Message body |
| channel | String(20) | email / sms / whatsapp |
| status | String(20) | draft / sent / failed |
| open_count | Integer | Email open tracking |
| click_count | Integer | Link click tracking |
| created_at | DateTime | |

### BusinessProfile
AI setup settings per business.

| Column | Type | Description |
|---|---|---|
| business_id | FK UNIQUE | |
| business_type | String(50) | restaurant / spa / gym / retail / etc. |
| tone | String(50) | friendly / professional / fun |
| peak_days | String(200) | e.g. "Friday,Saturday" |
| timezone | String(100) | e.g. "America/Chicago" |
| setup_complete | Boolean | Onboarding finished |
| welcome_msg_enabled | Boolean | Auto welcome SMS on signup |
| weekly_special_enabled | Boolean | Weekly AI campaign |
| flash_deal_enabled | Boolean | Slow day flash deals |
| birthday_enabled | Boolean | Birthday automations |
| winback_enabled | Boolean | 30-day win-back |
| google_review_enabled | Boolean | Review request automation |
| google_review_link | String(500) | Google review URL |

### Journey
Multi-step automation workflow.

| Column | Type | Description |
|---|---|---|
| id | Integer PK | |
| business_id | FK | |
| name | String(200) | Journey name |
| trigger | String(50) | signup / birthday / winback |
| steps | Text | JSON array of steps |
| active | Boolean | Is running |

### Product
Business product/service catalog shown on public page.

| Column | Type | Description |
|---|---|---|
| id | Integer PK | |
| business_id | FK | |
| name | String(200) | Product name |
| description | Text | Product description |
| price | String(50) | e.g. "$18.99" or "From $50" |
| category | String(100) | e.g. "Main Course" |
| image_url | String(500) | Uploaded photo |
| is_featured | Boolean | Show in Featured section |
| sort_order | Integer | Display order |
| active | Boolean | Visible on public page |

### CheckIn
QR code scan / public page join records.

| Column | Type | Description |
|---|---|---|
| id | Integer PK | |
| business_id | FK | |
| customer_id | FK → customers | |
| name | String(200) | Customer name at time of check-in |
| phone | String(50) | |
| source | String(30) | qr / manual |
| coupon_code | String(20) | Unique welcome coupon code |
| coupon_redeemed | Boolean | Has been used |
| coupon_redeemed_at | DateTime | When it was used |
| created_at | DateTime | |

### CampaignRedemption
Revenue tracked per campaign (ROI tracker).

| Column | Type | Description |
|---|---|---|
| id | Integer PK | |
| business_id | FK | |
| campaign_id | FK | |
| amount | Float | Revenue amount |
| note | String(300) | Staff note |
| created_at | DateTime | |

### SquareConnection
Square POS OAuth credentials per business.

| Column | Type | Description |
|---|---|---|
| business_id | FK UNIQUE | |
| access_token | Text | OAuth access token |
| merchant_id | String(100) | Square merchant ID |
| location_id | String(100) | Square location |

### SocialConnection
Facebook / Instagram page tokens.

| Column | Type | Description |
|---|---|---|
| business_id | FK | |
| platform | String(30) | facebook / instagram |
| page_id | String(100) | FB Page ID |
| page_name | String(200) | |
| access_token | Text | Long-lived page token |
| ig_user_id | String(100) | Instagram user ID |

---

## 4. Environment Variables

Set these in **Render → Environment**:

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | ✅ | PostgreSQL connection string (auto-set by Render) |
| `SECRET_KEY` | ✅ | Flask session secret (random string) |
| `OPENAI_API_KEY` | ✅ | OpenAI API key (starts with sk-) |
| `TWILIO_ACCOUNT_SID` | ✅ | Twilio account SID |
| `TWILIO_AUTH_TOKEN` | ✅ | Twilio auth token |
| `TWILIO_FROM_NUMBER` | ✅ | Twilio phone number (e.g. +15551234567) |
| `RESEND_API_KEY` | ✅ | Resend email API key |
| `RESEND_FROM_EMAIL` | ✅ | From email (e.g. hello@revvio.ai) |
| `STRIPE_SECRET_KEY` | ✅ | Stripe secret key |
| `STRIPE_PUBLISHABLE_KEY` | ✅ | Stripe publishable key |
| `STRIPE_WEBHOOK_SECRET` | ⚠️ | Stripe webhook signing secret |
| `META_APP_ID` | ⚠️ | Facebook App ID |
| `META_APP_SECRET` | ⚠️ | Facebook App Secret |
| `SQUARE_APP_ID` | ⚠️ | Square application ID |
| `SQUARE_APP_SECRET` | ⚠️ | Square app secret |
| `SQUARE_WEBHOOK_SIGNATURE_KEY` | ⚠️ | Square webhook key |
| `GOOGLE_CLIENT_ID` | ⚠️ | Google OAuth client ID |
| `GOOGLE_CLIENT_SECRET` | ⚠️ | Google OAuth secret |
| `ADMIN_EMAIL` | ✅ | Superadmin login email |
| `ADMIN_PASSWORD` | ✅ | Superadmin login password |
| `CRON_SECRET` | ✅ | Secret key for cron automation endpoint |

✅ = Required for core functionality  
⚠️ = Required for that specific integration

---

## 5. Feature Modules

### 5.1 Customer Management
- Add customers manually or via CSV/paste import
- Fields: name, email, phone, DOB, WhatsApp, source
- Segments: all, new, loyal, inactive, birthday, SMS-only, WhatsApp
- Duplicate detection and merge
- Unsubscribe / suppression list
- Individual customer profile with campaign history

### 5.2 Campaigns
- Create email, SMS, or WhatsApp messages
- AI-generated message content via OpenAI
- Schedule or send immediately
- Open tracking (email pixel), click tracking
- Campaign cloning, editing, deleting
- Bulk send to segments

### 5.3 AI Automations (`/setup`)
Toggle-based automations that run on a schedule:

| Automation | Trigger | Action |
|---|---|---|
| Welcome Message | New customer joins | Instant SMS/WhatsApp |
| Weekly Special | Every Tuesday | AI writes & sends offer |
| Flash Deal | Slow days | AI sends flash discount |
| Birthday Offer | Customer's birthday | Personalized SMS |
| Win-Back (30 days) | No visit in 30 days | Re-engagement SMS |
| Google Review Request | 3 days after signup | Review link SMS |
| Loyalty Milestones | Visit count hit | Reward message |

Cron endpoint: `GET /cron/run-automations?secret=CRON_SECRET`  
Triggered externally (e.g. cron-job.org) every 30 minutes.

### 5.4 Journeys
Visual multi-step automation builder:
- Trigger: signup / birthday / win-back / manual
- Steps: wait X days → send email/SMS → check open → branch
- Per-journey analytics (sent, opened, clicked)
- Audience enrollment

### 5.5 AI Social Posts (`/social-posts`)
- Describe a promotion → AI writes Instagram, Facebook, WhatsApp, Story posts
- Ready-made templates (no AI needed)
- Post directly to connected Facebook/Instagram pages
- Visual post queue with drag-to-reorder

### 5.6 Visual Post Designer (`/visual-posts`)
- 8 CSS-based design templates
- Fill in headline, subtext, badge, business name, address
- Download as PNG (html2canvas)
- Post directly to Facebook/Instagram via Meta Graph API

### 5.7 Public Landing Page (`/p/<slug>`)
Customer-facing page when they scan the QR code:
- Business hero (dark gradient, logo, name, address)
- Product/service catalog with photos and category filter
- Featured products section
- Join VIP list form (name, last name, phone, email, DOB)
- Welcome coupon shown after joining (if enabled)

### 5.8 QR Check-In (`/my-qr`)
- QR code generated via api.qrserver.com pointing to `/p/<slug>`
- Print-ready QR page
- Stats: today / week / all-time check-ins
- Recent check-ins list
- Blast SMS: send public page link to all existing customers
- Coupon analytics: issued, redeemed, pending, redemption rate

### 5.9 Welcome Coupon System
- Business configures in `/branding`: amount (e.g. $5 / ₹100), description, expiry days
- Customer joins → unique 10-character code generated
- Digital coupon card shown (gradient design, QR code, expiry date)
- SMS sent with code
- Staff scans QR → `/redeem/<code>` → verify + mark as redeemed
- Analytics on QR page

### 5.10 Revenue ROI (`/roi`)
- Log revenue manually per campaign
- Total revenue tracked, avg per visit
- Campaign-level ROI breakdown

### 5.11 Square POS Integration (`/square-connect`)
- OAuth2 connect flow
- Auto-logs revenue when payment.created webhook fires
- Manual sync of historical payments
- Matches payments to customers by email/phone

### 5.12 Branding & Permissions (`/branding`)
- Upload logo
- Set brand name (replaces "Revvio" in header)
- Pick brand color
- Custom subdomain field
- Feature toggles (hide/show per client)
- Welcome offer configuration

### 5.13 AI Business Assistant (`/ai-agent`)
- Full-page chat interface
- Context-aware: knows customer count, campaigns, plan
- Sidebar stats
- Floating widget on dashboard (bottom-right)
- Guides businesses through setup step by step

### 5.14 Onboarding Checklist (dashboard)
- 6-step checklist for new users
- Progress bar
- Steps: profile → customers → campaign → branding → QR → ROI
- Dismissable

---

## 6. All Routes / API Endpoints

### Public / Auth
| Route | Method | Description |
|---|---|---|
| `/` | GET | Landing page |
| `/register` | GET/POST | Business registration |
| `/login` | GET/POST | Login |
| `/logout` | GET | Logout, clear session |
| `/forgot-password` | GET/POST | Password reset |
| `/auth/google` | POST | Google OAuth login |
| `/privacy` | GET | Privacy policy |
| `/terms` | GET | Terms of service |
| `/r/<code>` | GET | Referral link redirect |

### Dashboard & Core
| Route | Method | Description |
|---|---|---|
| `/dashboard` | GET | Main dashboard |
| `/setup` | GET/POST | AI automations setup |
| `/settings` | GET/POST | Account settings |
| `/onboarding` | GET/POST | New user onboarding wizard |
| `/upgrade` | GET | Pricing / upgrade page |
| `/refer` | GET | Referral program |

### Customers
| Route | Method | Description |
|---|---|---|
| `/customers` | GET | Customer list |
| `/add-customer` | GET/POST | Add single customer |
| `/edit-customer/<id>` | POST | Update customer |
| `/delete-customer/<id>` | POST | Delete customer |
| `/upload-customers` | GET/POST | CSV import |
| `/paste-customers` | POST | Paste bulk import |
| `/customers/duplicates` | GET | Find duplicates |
| `/customers/merge` | POST | Merge duplicate records |
| `/customers/<id>/profile` | GET | Individual customer profile |
| `/suppression` | GET | Suppression/unsubscribe list |

### Campaigns
| Route | Method | Description |
|---|---|---|
| `/campaigns` | GET | Campaign list |
| `/create-campaign` | GET/POST | Create new campaign |
| `/campaign/<id>` | GET | Campaign detail |
| `/send-campaign/<id>` | POST | Send email campaign |
| `/send-sms/<id>` | POST | Send SMS campaign |
| `/edit-campaign/<id>` | POST | Edit campaign |
| `/clone-campaign/<id>` | POST | Duplicate campaign |
| `/delete-campaign/<id>` | POST | Delete campaign |
| `/bulk-send` | GET/POST | Bulk send to segment |
| `/quick-sms` | GET/POST | Quick single SMS |
| `/quick-whatsapp` | GET/POST | Quick WhatsApp |
| `/generate-message` | POST | AI generate message |
| `/track/open/<id>` | GET | Email open pixel |
| `/track/click/<id>` | GET | Click tracking redirect |
| `/unsubscribe/<token>` | GET | Customer unsubscribe |

### Social Media
| Route | Method | Description |
|---|---|---|
| `/social-posts` | GET | AI social posts page |
| `/generate-social-post` | POST | AI generate posts |
| `/visual-posts` | GET | Visual template designer |
| `/social-connect` | GET | Facebook/Instagram connect |
| `/facebook/callback` | GET | OAuth callback |
| `/facebook/disconnect` | POST | Disconnect Facebook |
| `/social-queue` | GET | Post scheduling queue |
| `/api/post-to-social` | POST | Publish to FB/Instagram |
| `/api/upload-post-image` | POST | Upload image for posting |
| `/api/save-to-queue` | POST | Save post to queue |
| `/api/reorder-queue` | POST | Reorder queue items |
| `/api/delete-queue-post/<id>` | POST | Remove from queue |
| `/ai-ideas` | POST | AI post ideas |

### Journeys & Audiences
| Route | Method | Description |
|---|---|---|
| `/journeys` | GET | Journey list |
| `/journeys/create` | GET/POST | Create journey |
| `/journeys/<id>/toggle` | POST | Enable/disable |
| `/journeys/<id>/delete` | POST | Delete |
| `/journeys/<id>/analytics` | GET | Journey stats |
| `/journeys/<id>/logs` | GET | Step execution logs |
| `/journeys/<id>/test` | GET/POST | Test journey |
| `/audiences` | GET | Audience list |
| `/audiences/create` | GET/POST | Create audience |
| `/audiences/<id>/enroll` | POST | Enroll customers |
| `/audiences/<id>/preview` | GET | Preview members |

### Public Page & QR
| Route | Method | Description |
|---|---|---|
| `/p/<slug>` | GET | Public business landing page |
| `/api/do-checkin/<slug>` | POST | Process join form submission |
| `/my-qr` | GET | QR dashboard + analytics |
| `/redeem/<code>` | GET | Staff coupon verification page |
| `/api/redeem-coupon/<code>` | POST | Mark coupon as redeemed |
| `/api/blast-page-link` | POST | SMS blast to all customers |
| `/join/<slug>` | GET/POST | Legacy join page |
| `/checkin/<token>` | GET | Per-customer QR checkin |

### Products
| Route | Method | Description |
|---|---|---|
| `/products` | GET | Product management page |
| `/api/products` | POST | Add product |
| `/api/products/<id>` | PUT | Update product |
| `/api/products/<id>` | DELETE | Delete product |
| `/api/upload-product-image/<id>` | POST | Upload product photo |

### Branding & Settings
| Route | Method | Description |
|---|---|---|
| `/branding` | GET/POST | White-label + permissions + welcome offer |
| `/api/dismiss-onboarding` | POST | Mark onboarding done |

### Revenue & Square
| Route | Method | Description |
|---|---|---|
| `/roi` | GET | Revenue ROI dashboard |
| `/api/log-redemption` | POST | Log manual revenue |
| `/square-connect` | GET | Square POS connect page |
| `/square/callback` | GET | Square OAuth callback |
| `/square/disconnect` | POST | Disconnect Square |
| `/square/sync-payments` | POST | Manual payment sync |
| `/api/square-webhook` | POST | Square webhook receiver |

### AI Assistant
| Route | Method | Description |
|---|---|---|
| `/ai-agent` | GET | AI assistant full page |
| `/api/ai-agent-chat` | POST | Chat with AI assistant |

### Automations & Cron
| Route | Method | Description |
|---|---|---|
| `/automations` | GET/POST | Automation settings |
| `/autopilot` | GET | Autopilot overview |
| `/autopilot/test` | POST | Test automation run |
| `/cron/run-automations` | GET | Cron trigger (external) |
| `/twilio/webhook` | POST | Inbound SMS handler |

### Admin
| Route | Method | Description |
|---|---|---|
| `/admin` | GET | Admin redirect |
| `/admin/login` | GET/POST | Admin login |
| `/admin/dashboard` | GET | All businesses overview |
| `/admin/add-business` | POST | Create business account |
| `/admin/change-plan/<id>` | POST | Change business plan |
| `/admin/login-as/<id>` | POST | Impersonate business |
| `/admin/delete-business/<id>` | POST | Delete business |
| `/admin/send-message` | POST | Message a business |
| `/admin/campaign-types/*` | POST | Manage campaign types |

---

## 7. User Flows

### New Business Registration
```
/register → fill form → email verified → /onboarding (AI setup wizard)
→ add business type, tone, peak days, automations
→ /dashboard (with onboarding checklist)
```

### Customer Acquires via QR
```
Customer scans QR code
→ /p/<slug> (public landing page)
→ sees products, fills name/phone/email/DOB
→ POST /api/do-checkin/<slug>
→ Customer created in DB
→ Welcome SMS sent (if Twilio configured)
→ Coupon card shown (if welcome offer enabled)
→ SMS with coupon code sent
```

### Staff Redeems Coupon
```
Customer shows coupon QR
→ Staff scans with phone → /redeem/<code>
→ Sees customer name, join date, offer
→ Clicks "Mark as Redeemed"
→ POST /api/redeem-coupon/<code>
→ Marked as used in DB
```

### Send AI Campaign
```
/create-campaign → select type + channel
→ POST /generate-message (AI writes content)
→ Review message
→ POST /send-campaign/<id> or /send-sms/<id>
→ Sent via Resend (email) or Twilio (SMS)
→ Open/click tracked
```

### Generate & Post Social Content
```
/social-posts → describe promotion
→ POST /generate-social-post (OpenAI, ~10s)
→ Shows Instagram, Facebook, WhatsApp, Story content
→ Click "Post to Facebook" → POST /api/post-to-social
→ Publishes via Meta Graph API v18.0
```

---

## 8. Plans & Permissions

| Feature | Free | Pro | Enterprise |
|---|---|---|---|
| Customers | 100 max | 2,000 max | Unlimited |
| Campaigns | 5/month | 50/month | Unlimited |
| AI Social Posts | ✅ | ✅ | ✅ |
| Email Campaigns | ✅ | ✅ | ✅ |
| WhatsApp | ❌ | ✅ | ✅ |
| Journeys | ❌ | ✅ | ✅ |
| QR Check-In | ❌ | ✅ | ✅ |
| Visual Templates | ❌ | ✅ | ✅ |
| Revenue ROI | ❌ | ✅ | ✅ |
| AI Assistant | ❌ | ✅ | ✅ |
| Square POS | ❌ | ❌ | ✅ |
| White-Label | ❌ | ❌ | ✅ |

Per-business feature overrides can be set in `/branding` (admin can hide features per client regardless of plan).

---

## 9. Third-Party Integrations

### OpenAI (gpt-4o-mini)
- Used for: campaign messages, social posts, AI assistant
- Called via: `requests.post` (not openai SDK, avoids httpx conflicts)
- Timeout: 20 seconds, max_tokens: 350
- Helper: `_call_openai(prompt, max_tokens, timeout)`

### Twilio
- Used for: SMS, WhatsApp messages
- Environment: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`
- Inbound SMS: `POST /twilio/webhook`

### Resend
- Used for: Email delivery
- From address: `RESEND_FROM_EMAIL` env var
- Supports custom domain email per client

### Stripe
- Used for: Pro/Enterprise subscriptions
- Webhooks: payment confirmation, subscription events
- Plans: Starter ($19/mo), Pro ($50/mo)

### Meta Graph API v18.0
- Used for: Post to Facebook Pages and Instagram Business accounts
- OAuth flow: `/social-connect` → `/facebook/callback`
- Stores long-lived page token per business

### Square
- Used for: Auto-log revenue from POS payments
- OAuth2 flow: `/square-connect` → `/square/callback`
- Webhook: `POST /api/square-webhook` (payment.created events)

### Google OAuth2
- Used for: "Sign in with Google" on registration/login
- Endpoint: `POST /auth/google`

---

## 10. Deployment

**Platform:** Render.com (PaaS)

**Procfile:**
```
web: gunicorn main:app --bind 0.0.0.0:$PORT --workers 1 --timeout 120 --preload
```

**Important notes:**
- 1 worker only — required because background job state uses in-memory storage
- Timeout 120s — allows longer AI operations
- Render load balancer has its own 30s HTTP timeout for free tier

**Database migrations:**
Run automatically at startup in `_do_migrations()` (background thread, 2s delay).
Uses `ALTER TABLE IF NOT EXISTS` — safe to run repeatedly.

**Static files:**
- `/static/logos/` — uploaded business logos
- `/static/product-images/` — product photos
- `/static/post-images/` — generated social post images

> ⚠️ Static file uploads are lost on Render redeploy (ephemeral filesystem). For production, move to S3 or Cloudflare R2.

**Deploying updates:**
```bash
git add .
git commit -m "description"
git push
# Render auto-deploys on push to main branch
```

---

## 11. Admin Panel

Access: `/admin/login`  
Credentials: `ADMIN_EMAIL` + `ADMIN_PASSWORD` environment variables

**Capabilities:**
- View all businesses, customer counts, campaign counts, plan
- Change any business's plan (free/pro/enterprise)
- Login as any business (impersonation for support)
- Add new business accounts manually
- Delete business accounts
- Send messages to businesses
- Manage campaign types (add/edit/delete categories)
- View MRR, trial expiry alerts

**Session isolation:**
Admin session is stored separately from business session. Impersonating a business sets a temporary session flag.

---

## Quick Reference — Key Helper Functions

| Function | Location | Purpose |
|---|---|---|
| `current_business()` | main.py | Get logged-in business from session |
| `has_feature(b, feature)` | main.py | Check plan + feature permissions |
| `brand(b)` | main.py | Get white-label name/color/logo |
| `_call_openai(prompt, max_tokens, timeout)` | main.py | Call OpenAI API |
| `_send_sms(to, body)` | main.py | Send SMS via Twilio |
| `get_segment(business_id, segment)` | main.py | Filter customers by segment |
| `trial_days_left(b)` | main.py | Days remaining in trial |
| `make_slug(name, uid)` | main.py | Generate URL-safe slug |

---

*This documentation covers Revvio v1.0 — April 2026.*  
*For questions, contact the development team.*
