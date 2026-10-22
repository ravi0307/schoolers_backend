"""Shared subject, body, and signature rendering for every Schoolers email.

Every email is described once as structured data — greeting, paragraphs,
detail bullets, numbered steps, closing note, signature — and rendered twice
from that single description: once as plain text and once as escaped HTML.
Both alternatives therefore always communicate the same information, and no
caller can accidentally ship an HTML email without a text part.

Safety rules encoded here:
- Every value interpolated into HTML goes through html.escape, so names,
  announcements and remarks from the database can never inject markup.
- Subject lines are collapsed to a single line and truncated at a word
  boundary, so a multi-line announcement can never become header injection.
- Signatures skip missing contact details instead of printing placeholders.
"""
from html import escape as html_escape

PLATFORM_SENDER = "Schoolers Team"
PLATFORM_TAGLINE = "School Management Platform"


def collapse(value: object) -> str:
    """Collapse any value to a single safe line (for subjects and headers)."""
    return " ".join(str(value).split())


def truncate_words(text: str, limit: int = 60) -> str:
    """Truncate at a word boundary so a subject stays readable."""
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:. -")
    return f"{cut}\u2026" if cut else text[:limit]


def platform_subject(purpose: str) -> str:
    """Subject for platform-level mail (no single school is the actor)."""
    return f"Schoolers | {collapse(purpose)}"


def school_subject(school_name: str, purpose: str) -> str:
    """Subject for school-context mail: '{Verified School} | {Purpose}'."""
    return f"{collapse(school_name)} | {collapse(purpose)}"


def platform_signature() -> list[str]:
    """Sender block for platform-operated mail (resets, school lifecycle)."""
    return ["Regards,", "", PLATFORM_SENDER, PLATFORM_TAGLINE]


def school_signature(
    school_name: object,
    contact_email: object = None,
    contact_phone: object = None,
) -> list[str]:
    """Sender block for school mail, using only verified contact details.

    Lines are appended only when a value exists, so a school with no phone
    on file never sees an empty placeholder line.
    """
    lines = ["Regards,", "", "School Administration", collapse(school_name)]
    email = collapse(contact_email) if contact_email else ""
    phone = collapse(contact_phone) if contact_phone else ""
    if email:
        lines.append(email)
    if phone:
        lines.append(phone)
    return lines


def render_text(
    greeting: str,
    paragraphs: list[str] | tuple[str, ...] = (),
    details: list[str] | tuple[str, ...] = (),
    steps: list[str] | tuple[str, ...] = (),
    closing: str = "",
    signature: list[str] | tuple[str, ...] = (),
) -> str:
    """Plain-text body from the shared structure."""
    blocks: list[str] = [collapse(greeting)]
    blocks.extend(collapse(p) for p in paragraphs if collapse(p))
    if details:
        blocks.append("Details:\n" + "\n".join(f"  \u2022 {collapse(d)}" for d in details))
    if steps:
        blocks.append("Next steps:\n" + "\n".join(f"  {i}. {collapse(s)}" for i, s in enumerate(steps, 1)))
    if closing:
        blocks.append(collapse(closing))
    blocks.append("\n".join(signature))
    return "\n\n".join(blocks)


def render_html(
    greeting: str,
    paragraphs: list[str] | tuple[str, ...] = (),
    details: list[str] | tuple[str, ...] = (),
    steps: list[str] | tuple[str, ...] = (),
    closing: str = "",
    signature: list[str] | tuple[str, ...] = (),
) -> str:
    """HTML alternative of the same structure — every value escaped."""
    body: list[str] = [f'<p style="margin:0 0 14px;">{html_escape(collapse(greeting))}</p>']
    for paragraph in paragraphs:
        text = collapse(paragraph)
        if text:
            body.append(f'<p style="margin:0 0 14px;">{html_escape(text)}</p>')
    if details:
        items = "".join(
            f'<li style="margin:0 0 6px;">{html_escape(collapse(d))}</li>' for d in details
        )
        body.append(
            '<p style="margin:14px 0 6px;font-weight:bold;">Details:</p>'
            f'<ul style="margin:0 0 14px;padding-left:20px;">{items}</ul>'
        )
    if steps:
        items = "".join(
            f'<li style="margin:0 0 6px;">{html_escape(collapse(s))}</li>' for s in steps
        )
        body.append(
            '<p style="margin:14px 0 6px;font-weight:bold;">Next steps:</p>'
            f'<ol style="margin:0 0 14px;padding-left:20px;">{items}</ol>'
        )
    if closing:
        body.append(f'<p style="margin:0 0 14px;">{html_escape(collapse(closing))}</p>')
    sig_lines = list(signature)
    sig_html = "".join(
        (
            f'<p style="margin:0;">{html_escape(line)}</p>' if line
            else '<p style="margin:0 0 10px;">&nbsp;</p>'
        )
        for line in sig_lines
    )
    # Bold the sender-name line (first non-empty line after "Regards,").
    for index, line in enumerate(sig_lines):
        if index > 0 and line and sig_lines[index - 1] == "":
            sig_html = sig_html.replace(
                f"<p style=\"margin:0;\">{html_escape(line)}</p>",
                f'<p style="margin:0;font-weight:bold;">{html_escape(line)}</p>',
                1,
            )
            break
    content = "".join(body)
    return (
        '<!doctype html><html><body style="margin:0;padding:16px;background:#f4f5f7;'
        'font-family:Arial,Helvetica,sans-serif;">'
        '<div style="max-width:560px;margin:0 auto;background:#ffffff;border:1px '
        'solid #e5e7eb;border-radius:8px;padding:24px;color:#1f2937;font-size:15px;'
        'line-height:1.55;">'
        f"{content}"
        f'<div style="margin-top:18px;">{sig_html}</div>'
        "</div></body></html>"
    )


def render_email(
    greeting: str,
    paragraphs: list[str] | tuple[str, ...] = (),
    details: list[str] | tuple[str, ...] = (),
    steps: list[str] | tuple[str, ...] = (),
    closing: str = "",
    signature: list[str] | tuple[str, ...] = (),
) -> tuple[str, str]:
    """Return the (plain text, HTML) pair for one email."""
    kwargs = dict(
        greeting=greeting,
        paragraphs=paragraphs,
        details=details,
        steps=steps,
        closing=closing,
        signature=signature,
    )
    return render_text(**kwargs), render_html(**kwargs)
