"""A local copy of a Workday application flow, for trying the Workday plugin without touching an employer's site.

    python tests/sites/workday_local.py                 # serves http://127.0.0.1:8765
    AIHAWK_TEST_SMTP=smtp.gmail.com:587:you@gmail.com:app-password python tests/sites/workday_local.py

The pages carry the `data-automation-id` values read on a real Workday site on 2026-11-07, up to the "Create Account"
page: `applyManually`, `SignInWithEmailButton`, `email`, `password`, `signInSubmitButton`, `createAccountLink`,
`verifyPassword`, `createAccountCheckbox`, `createAccountSubmitButton`, the robots-only field `beecatcher`. The steps
after the account are Workday's usual form, as the plugin describes them, and are not read from a real site.

After "Create Account" the site asks for a six-digit code sent to the address: by SMTP when AIHAWK_TEST_SMTP is set,
otherwise the code is only printed here. The posting is /en-US/Careers/job/US-Remote/Software-Engineer_JR1.
What was submitted is listed at /applications as JSON, and /reset forgets every account and application.
"""

from __future__ import annotations

import html
import json
import os
import re
import secrets
import smtplib
import sys
import threading
import urllib.parse
from datetime import UTC, datetime
from email.message import EmailMessage
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST, PORT = "127.0.0.1", 8765
JOB_PATH = "/en-US/Careers/job/US-Remote/Software-Engineer_JR1"
STEPS = ["Create Account/Sign In", "My Information", "My Experience", "Application Questions", "Voluntary Disclosures", "Review"]
PASSWORD_RULES = (
    ("A minimum of 8 characters", lambda p: len(p) >= 8),
    ("An uppercase character", lambda p: re.search(r"[A-Z]", p)),
    ("A special character", lambda p: re.search(r"[^A-Za-z0-9]", p)),
    ("An alphabetic character", lambda p: re.search(r"[A-Za-z]", p)),
    ("A numeric character", lambda p: re.search(r"[0-9]", p)),
    ("A lowercase character", lambda p: re.search(r"[a-z]", p)),
)
STATE_LOCK = threading.Lock()
STATE: dict[str, dict] = {"accounts": {}, "codes": {}, "sessions": {}, "forms": {}, "applications": []}


def log(line: str) -> None:
    print(f"[workday-local {datetime.now(UTC):%H:%M:%S}] {line}", flush=True)


def send_code(email: str, code: str) -> str:
    """The code by mail when SMTP is configured, as host:port:user:password; what happened, for the log."""
    setting = os.environ.get("AIHAWK_TEST_SMTP", "").strip()
    if not setting:
        return f"no AIHAWK_TEST_SMTP: the code for {email} is {code} (not mailed)"
    host, port, user, password = setting.split(":", 3)
    message = EmailMessage()
    message["From"] = f"Example Careers (Workday test) <{user}>"
    message["To"] = email
    message["Subject"] = "Your verification code"
    message.set_content(
        f"Verify your email\n\nYour verification code is {code}.\n\nEnter it on the page to continue your application "
        f"for Software Engineer at Example. The code expires in 10 minutes.\n\nThis message was sent by the local Workday test site of AIHawk."
    )
    with smtplib.SMTP(host, int(port), timeout=30) as smtp:
        smtp.starttls()
        smtp.login(user, password)
        smtp.send_message(message)
    return f"code mailed to {email} from {user}"


def page(title: str, body: str, step: int | None = None) -> bytes:
    bar = ""
    if step is not None:
        items = "".join(
            f'<li data-automation-id="{"progressBarActiveStep" if index == step else "progressBarInactiveStep"}">'
            f'{"current " if index == step else ""}step {index + 1} of {len(STEPS)} {name}</li>'
            for index, name in enumerate(STEPS)
        )
        bar = (
            f'<button data-automation-id="backToJobPosting" type="button" onclick="location.href=\'{JOB_PATH}\'">Back to Job Posting</button>'
            f'<h2 data-automation-id="jobTitleHeading">Software Engineer</h2><ol data-automation-id="progressBar">{items}</ol>'
        )
    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>body{{font-family:Arial,sans-serif;max-width:760px;margin:24px auto;padding:0 16px}}label{{display:block;margin:12px 0 4px}}
input,select{{padding:6px;min-width:280px}}button{{padding:8px 16px;margin:12px 8px 0 0}}.error{{color:#b00020}}ol{{display:flex;gap:16px;list-style:none;padding:0;font-size:13px}}
[data-automation-id=beecatcher]{{position:absolute;left:-9999px}}.muted{{color:#666;font-size:13px}}</style></head><body>
<div data-automation-id="header"><div data-automation-id="headerTitle">CAREERS AT EXAMPLE</div>
<button data-automation-id="utilityButtonSignIn" type="button" onclick="location.href='/en-US/Careers/login'">Sign In</button></div>
{bar}{body}
<div data-automation-id="footerContainer" class="muted">Local copy of a Workday flow, for AIHawk's tests. © Workday, Inc. look-alike, no affiliation.</div>
</body></html>"""
    return doc.encode("utf-8")


def errors(lines: list[str]) -> str:
    if not lines:
        return ""
    return '<div data-automation-id="errorMessage" class="error" role="alert"><b>Error:</b><ul>' + "".join(f"<li>{html.escape(line)}</li>" for line in lines) + "</ul></div>"


def honeypot() -> str:
    return '<input data-automation-id="beecatcher" type="text" name="website" tabindex="-1" autocomplete="off" aria-label="Enter website. This input is for robots only, do not enter if you\'re human.">'


class Handler(BaseHTTPRequestHandler):
    server_version = "WorkdayLocal/1"

    # --- plumbing
    def log_message(self, format: str, *args: object) -> None:
        log(f"{self.command} {self.path} {args[1] if len(args) > 1 else ''}")

    def reply(self, body: bytes, status: int = 200, headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def redirect(self, where: str, headers: dict[str, str] | None = None) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", where)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def session_email(self) -> str:
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        token = cookie["wd_session"].value if "wd_session" in cookie else ""
        with STATE_LOCK:
            return STATE["sessions"].get(token, "")

    def form(self) -> dict[str, str]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        kind = self.headers.get("Content-Type", "")
        if kind.startswith("multipart/form-data"):
            boundary = kind.split("boundary=", 1)[1].encode()
            fields: dict[str, str] = {}
            for part in raw.split(b"--" + boundary)[1:]:
                if part.strip() in (b"", b"--"):
                    continue
                head, _, content = part.partition(b"\r\n\r\n")
                name = re.search(rb'name="([^"]+)"', head)
                filename = re.search(rb'filename="([^"]*)"', head)
                if name:
                    value = filename.group(1).decode() if filename else content.rstrip(b"\r\n").decode("utf-8", "replace")
                    fields[name.group(1).decode()] = value
            return fields
        return {key: values[-1] for key, values in urllib.parse.parse_qs(raw.decode("utf-8"), keep_blank_values=True).items()}

    # --- GET
    def do_GET(self) -> None:
        path = urllib.parse.urlsplit(self.path).path.rstrip("/")
        if path == JOB_PATH:
            return self.posting()
        if path == JOB_PATH + "/apply":
            return self.apply_start()
        if path == JOB_PATH + "/apply/applyManually":
            return self.apply_flow()
        if path == "/en-US/Careers/login":
            return self.reply(page("Sign In", self.sign_in_options()))
        if path == JOB_PATH + "/apply/submitted":
            return self.reply(page("Application Submitted", '<div data-automation-id="applySuccess"><h2>Your application has been submitted</h2><p>Thank you for applying to Software Engineer at Example. You will hear from us by email.</p></div>'))
        if path == "/applications":
            with STATE_LOCK:
                body = json.dumps(STATE["applications"], indent=1, ensure_ascii=False).encode("utf-8")
            return self.reply(body, headers={"Content-Type": "application/json; charset=utf-8"})
        if path == "/reset":
            with STATE_LOCK:
                for table in STATE.values():
                    table.clear()
            log("state reset")
            return self.reply(b"reset")
        self.reply(page("Not Found", "<h2>Page not found</h2>"), 404)

    def posting(self) -> None:
        self.reply(page("Software Engineer", f"""
<div data-automation-id="jobPostingPage"><h2 data-automation-id="jobPostingHeader">Software Engineer</h2>
<a data-automation-id="adventureButton" href="{JOB_PATH}/apply" role="button">Apply</a>
<div data-automation-id="job-posting-details"><div data-automation-id="locations">locations US, Remote</div>
<div data-automation-id="time">time type Full time</div><div data-automation-id="requisitionId">job requisition id JR1</div></div>
<div data-automation-id="jobPostingDescription"><p>Example builds developer tools. You will design backend services in Python and Go, own their deployment on Kubernetes, and work with a small team across Europe and the US.</p>
<p>What we need: 5+ years with Python or Go, cloud infrastructure (AWS or GCP), and CI/CD. Nice: distributed systems, open source.</p></div></div>"""))

    def apply_start(self) -> None:
        self.reply(page("Software Engineer", f"""
<div data-automation-id="applyAdventurePage"><h2>Start Your Application</h2><p>Software Engineer</p>
<a data-automation-id="autofillWithResume" href="{JOB_PATH}/apply/autofillWithResume">Autofill with Resume</a><br>
<a data-automation-id="applyManually" href="{JOB_PATH}/apply/applyManually">Apply Manually</a><br>
<a data-automation-id="useMyLastApplication" href="{JOB_PATH}/apply/useMyLastApplication">Use My Last Application</a>
<iframe data-automation-id="applyWithLinkedIn" title="Apply with LinkedIn" srcdoc="&lt;button&gt;Apply with LinkedIn&lt;/button&gt;" style="display:block;border:0;height:48px"></iframe></div>"""))

    def sign_in_options(self) -> str:
        return f"""<div data-automation-id="signInContent"><h2>Sign In</h2>
<form method="post" action="{JOB_PATH}/apply/applyManually">
<button data-automation-id="GoogleSignInButton" type="button" onclick="alert('Not available on the local copy')">Sign in with Google</button>
<button data-automation-id="LinkedInSignInButton" type="button" onclick="alert('Not available on the local copy')">Sign in with LinkedIn</button>
<p>OR</p><button data-automation-id="SignInWithEmailButton" type="submit" name="view" value="email">Sign in with email</button></form></div>"""

    def apply_flow(self) -> None:
        email = self.session_email()
        if not email:
            return self.reply(page("Sign In", self.sign_in_options(), step=0))
        with STATE_LOCK:
            step = STATE["forms"].setdefault(email, {}).get("step", 1)
        self.reply(page(STEPS[step], self.step_body(step, email, {}, []), step=step))

    # --- POST
    def do_POST(self) -> None:
        path = urllib.parse.urlsplit(self.path).path.rstrip("/")
        if path != JOB_PATH + "/apply/applyManually":
            return self.reply(page("Not Found", "<h2>Page not found</h2>"), 404)
        fields = self.form()
        if fields.get("website"):
            log("the robots-only field was filled: refused")
            return self.reply(page("Sign In", errors(["Something went wrong. Please try again."]) + self.sign_in_options(), step=0))
        view = fields.get("view", "")
        if view == "email":
            return self.reply(page("Sign In", self.email_form([]), step=0))
        if view == "create":
            return self.reply(page("Create Account", self.create_form([], fields.get("email", "")), step=0))
        if view == "signin":
            return self.sign_in(fields)
        if view == "createAccount":
            return self.create_account(fields)
        if view == "verify":
            return self.verify(fields)
        if view == "resend":
            return self.resend(fields)
        if view.startswith("step"):
            return self.save_step(int(view[4:]), fields)
        self.reply(page("Sign In", self.sign_in_options(), step=0))

    def email_form(self, problems: list[str], email: str = "") -> str:
        return f"""<div data-automation-id="signInContent"><h2>Sign In</h2>{errors(problems)}
<form method="post" data-automation-id="signInFormo"><input type="hidden" name="view" value="signin">
<label>Email Address*<input data-automation-id="email" type="text" name="email" value="{html.escape(email)}"></label>
<label>Password*<input data-automation-id="password" type="password" name="password"></label>{honeypot()}
<button data-automation-id="signInSubmitButton" type="submit">Sign In</button></form>
<form method="post"><input type="hidden" name="view" value="create"><input type="hidden" name="email" value="{html.escape(email)}">
<span>Don't have an account yet?</span><button data-automation-id="createAccountLink" type="submit">Create Account</button></form>
<button data-automation-id="forgotPasswordLink" type="button">Forgot your password?</button></div>"""

    def create_form(self, problems: list[str], email: str = "") -> str:
        rules = "".join(f"<li>{name}</li>" for name, _ in PASSWORD_RULES)
        return f"""<div data-automation-id="signInContent"><h2>Create Account</h2>
<div data-automation-id="passwordRulesList"><b>Password Requirements:</b><ul>{rules}</ul></div>{errors(problems)}
<form method="post" data-automation-id="signInFormo"><input type="hidden" name="view" value="createAccount">
<div data-automation-id="formField-email"><label>Email Address*<input data-automation-id="email" type="text" name="email" value="{html.escape(email)}"></label></div>
<div data-automation-id="formField-password"><label>Password*<input data-automation-id="password" type="password" name="password"></label></div>
<div data-automation-id="formField-verifyPassword"><label>Verify New Password*<input data-automation-id="verifyPassword" type="password" name="verifyPassword"></label></div>
<p class="muted">To maintain the integrity of our hiring process, each candidate is permitted to maintain only one authentic profile.</p>
<div data-automation-id="formField-"><label><input data-automation-id="createAccountCheckbox" type="checkbox" name="agree" value="on"> I agree. I understand I am creating a candidate account, and my information will be stored by Example. By selecting the checkbox, you are agreeing to the Terms and Conditions and acknowledging our Applicant Privacy Policy.</label></div>
{honeypot()}<button data-automation-id="createAccountSubmitButton" type="submit">Create Account</button></form>
<form method="post"><input type="hidden" name="view" value="email"><span>Already have an account?</span><button data-automation-id="signInLink" type="submit">Sign In</button></form></div>"""

    def verify_form(self, email: str, problems: list[str]) -> str:
        return f"""<div data-automation-id="verifyEmailContent"><h2>Verify Your Email</h2>{errors(problems)}
<p>We sent a verification code to <b>{html.escape(email)}</b>. Enter the code below to continue. It expires in 10 minutes.</p>
<form method="post"><input type="hidden" name="view" value="verify"><input type="hidden" name="email" value="{html.escape(email)}">
<label>Verification Code*<input data-automation-id="verificationCode" type="text" name="code" inputmode="numeric" autocomplete="one-time-code"></label>
{honeypot()}<button data-automation-id="verifyButton" type="submit">Verify</button></form>
<form method="post"><input type="hidden" name="view" value="resend"><input type="hidden" name="email" value="{html.escape(email)}">
<button data-automation-id="resendCodeLink" type="submit">Resend code</button></form></div>"""

    def sign_in(self, fields: dict[str, str]) -> None:
        email, password = fields.get("email", "").strip().lower(), fields.get("password", "")
        with STATE_LOCK:
            known = STATE["accounts"].get(email)
        if not email or not password:
            return self.reply(page("Sign In", self.email_form(["Email Address and Password are required."], email), step=0))
        if known is None:
            return self.reply(page("Sign In", self.email_form(["The account does not exist. Create an account to continue."], email), step=0))
        if known["password"] != password:
            return self.reply(page("Sign In", self.email_form(["Invalid email address or password."], email), step=0))
        if not known["verified"]:
            return self.start_verification(email, "sign-in of an unverified account")
        return self.open_session(email)

    def create_account(self, fields: dict[str, str]) -> None:
        email, password, again = fields.get("email", "").strip().lower(), fields.get("password", ""), fields.get("verifyPassword", "")
        problems = []
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            problems.append("Enter a valid Email Address.")
        failed = [name for name, rule in PASSWORD_RULES if not rule(password)]
        if failed:
            problems.append("The password does not meet the requirements: " + ", ".join(failed) + ".")
        if password != again:
            problems.append("The passwords do not match.")
        if fields.get("agree") != "on":
            problems.append("You must agree to the Terms and Conditions.")
        with STATE_LOCK:
            exists = email in STATE["accounts"]
        if exists:
            problems.append("An account with this email address already exists. Sign in instead.")
        if problems:
            return self.reply(page("Create Account", self.create_form(problems, email), step=0))
        with STATE_LOCK:
            STATE["accounts"][email] = {"password": password, "verified": False, "created": datetime.now(UTC).isoformat(timespec="seconds")}
        log(f"account created for {email}")
        return self.start_verification(email, "account creation")

    def start_verification(self, email: str, why: str) -> None:
        code = f"{secrets.randbelow(900000) + 100000}"
        with STATE_LOCK:
            STATE["codes"][email] = code
        try:
            log(f"{why}: {send_code(email, code)}")
        except Exception as error:  # noqa: BLE001  The page still comes; the log says the mail did not go.
            log(f"{why}: the code could not be mailed: {error}")
        self.reply(page("Verify Your Email", self.verify_form(email, []), step=0))

    def resend(self, fields: dict[str, str]) -> None:
        self.start_verification(fields.get("email", "").strip().lower(), "resend")

    def verify(self, fields: dict[str, str]) -> None:
        email, code = fields.get("email", "").strip().lower(), re.sub(r"\D", "", fields.get("code", ""))
        with STATE_LOCK:
            expected = STATE["codes"].get(email)
        if not code or code != expected:
            log(f"wrong code for {email}: {code!r}")
            return self.reply(page("Verify Your Email", self.verify_form(email, ["The verification code is not valid. Check the code and try again."]), step=0))
        with STATE_LOCK:
            STATE["accounts"][email]["verified"] = True
            STATE["codes"].pop(email, None)
        log(f"{email} verified with the code")
        return self.open_session(email)

    def open_session(self, email: str) -> None:
        token = secrets.token_hex(16)
        with STATE_LOCK:
            STATE["sessions"][token] = email
            STATE["forms"].setdefault(email, {"step": 1})
        self.redirect(JOB_PATH + "/apply/applyManually", {"Set-Cookie": f"wd_session={token}; Path=/; HttpOnly"})

    # --- the six steps
    def step_body(self, step: int, email: str, values: dict[str, str], problems: list[str]) -> str:
        v = {key: html.escape(value) for key, value in values.items()}

        def text(aid: str, label: str, required: bool = True) -> str:
            return f'<label>{label}{"*" if required else ""}<input data-automation-id="{aid}" type="text" name="{aid}" value="{v.get(aid, "")}"></label>'

        def select(aid: str, label: str, options: list[str], required: bool = True) -> str:
            opts = "".join(f'<option value="{html.escape(o)}"{" selected" if v.get(aid) == html.escape(o) else ""}>{html.escape(o)}</option>' for o in ["Select One", *options])
            return f'<label>{label}{"*" if required else ""}<select data-automation-id="{aid}" name="{aid}">{opts}</select></label>'

        nav = f'<button data-automation-id="bottom-navigation-next-button" type="submit">{"Submit" if step == 5 else "Save and Continue"}</button>'
        head = f'<form method="post" enctype="multipart/form-data"><input type="hidden" name="view" value="step{step}">{errors(problems)}'
        if step == 1:
            body = f"""<h2>My Information</h2>{select("source", "How Did You Hear About Us?", ["Company Website", "LinkedIn", "Referral", "Other"])}
<fieldset><legend>Have you previously worked for Example?*</legend>
<label><input type="radio" name="previousWorker" value="yes" data-automation-id="previousWorker"> Yes</label>
<label><input type="radio" name="previousWorker" value="no" data-automation-id="previousWorker"> No</label></fieldset>
{select("countryDropdown", "Country", ["Italy", "United States of America", "Germany", "France", "Spain", "United Kingdom"])}
<h3>Legal Name</h3>{text("legalNameSection_firstName", "First Name")}{text("legalNameSection_lastName", "Last Name")}
<h3>Address</h3>{text("addressSection_addressLine1", "Address Line 1")}{text("addressSection_city", "City")}{text("addressSection_countryRegion", "State / Region")}{text("addressSection_postalCode", "Postal Code")}
<h3>Phone</h3>{select("phone-device-type", "Phone Device Type", ["Mobile", "Landline"])}{select("country-phone-code", "Country Phone Code", ["Italy (+39)", "United States of America (+1)", "Germany (+49)"])}{text("phone-number", "Phone Number")}"""
        elif step == 2:
            body = f"""<h2>My Experience</h2><h3>Work Experience</h3><p class="muted">Optional.</p><button data-automation-id="Add" type="button" onclick="this.insertAdjacentHTML('afterend','<p>Added a blank experience block (optional).</p>')">Add</button>
<h3>Education</h3><p class="muted">Optional.</p><button data-automation-id="Add Education" type="button">Add</button>
<h3>Resume/CV*</h3><p>Upload a file (PDF, DOC, DOCX, TXT).</p><input data-automation-id="file-upload-input-ref" type="file" name="resume">
<h3>Websites</h3><p class="muted">Optional.</p>{text("website", "Website", required=False)}"""
        elif step == 3:
            body = f"""<h2>Application Questions</h2>{select("workAuthorization", "Are you legally authorized to work in the country of this job?", ["Yes", "No"])}
{select("sponsorship", "Will you now or in the future require sponsorship for employment visa status?", ["Yes", "No"])}
{select("startDate", "When could you start?", ["Immediately", "Within 1 month", "Within 3 months", "More than 3 months"])}"""
        elif step == 4:
            body = f"""<h2>Voluntary Disclosures</h2><p class="muted">Completion of this form is voluntary.</p>
{select("gender", "Gender", ["Male", "Female", "I do not wish to answer"], required=False)}
{select("ethnicity", "Ethnicity", ["Asian", "Black or African American", "Hispanic or Latino", "White", "Two or More Races", "I do not wish to answer"], required=False)}
{select("veteranStatus", "Veteran Status", ["I am not a veteran", "I am a veteran", "I do not wish to answer"], required=False)}
<h3>Terms and Conditions*</h3><label><input data-automation-id="agreementCheckbox" type="checkbox" name="terms" value="on"> I have read and consent to the terms and conditions.</label>"""
        else:
            with STATE_LOCK:
                saved = STATE["forms"].get(email, {})
            rows = "".join(f"<tr><td>{html.escape(k)}</td><td>{html.escape(str(val))}</td></tr>" for k, val in saved.items() if k != "step")
            body = f'<h2>Review</h2><p>Review your information, then submit your application.</p><table data-automation-id="reviewTable">{rows}</table>'
        return head + body + nav + "</form>"

    def save_step(self, step: int, fields: dict[str, str]) -> None:
        email = self.session_email()
        if not email:
            return self.reply(page("Sign In", self.sign_in_options(), step=0))
        required = {
            1: ["source", "previousWorker", "countryDropdown", "legalNameSection_firstName", "legalNameSection_lastName", "addressSection_addressLine1", "addressSection_city", "addressSection_postalCode", "phone-device-type", "phone-number"],
            2: ["resume"],
            3: ["workAuthorization", "sponsorship", "startDate"],
            4: ["terms"],
            5: [],
        }[step]
        problems = [f"{name} is required." for name in required if fields.get(name, "").strip() in ("", "Select One")]
        if problems:
            return self.reply(page(STEPS[step], self.step_body(step, email, fields, problems), step=step))
        with STATE_LOCK:
            saved = STATE["forms"].setdefault(email, {"step": 1})
            saved.update({key: value for key, value in fields.items() if key not in ("view", "website")})
            if step < 5:
                saved["step"] = step + 1
                nxt = step + 1
            else:
                STATE["applications"].append({"email": email, "at": datetime.now(UTC).isoformat(timespec="seconds"), **{k: v for k, v in saved.items() if k != "step"}})
                STATE["forms"].pop(email, None)
        if step < 5:
            log(f"{email}: step {step + 1} saved")
            return self.reply(page(STEPS[nxt], self.step_body(nxt, email, {}, []), step=nxt))
        log(f"{email}: application submitted")
        return self.redirect(JOB_PATH + "/apply/submitted")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    log(f"serving http://{HOST}:{PORT}{JOB_PATH}  (SMTP {'set' if os.environ.get('AIHAWK_TEST_SMTP') else 'not set: codes are printed here'})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
