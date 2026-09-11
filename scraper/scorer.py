def calculate_score(data):

    score = 0
    reasons = []
    opportunities = []

    # ========================================================
    # WEBSITE
    # ========================================================

    if data.get("website_status") == "broken":

        score += 30

        reasons.append(
            "Website is broken"
        )

        opportunities.append(
            "Website redesign"
        )

    # ========================================================
    # SECURITY
    # ========================================================

    if (
        data.get("website_status") == "working"
        and not data.get("https")
    ):

        score += 10

        reasons.append(
            "Website does not use HTTPS"
        )

        opportunities.append(
            "SSL/security setup"
        )

    # ========================================================
    # MOBILE
    # ========================================================

    if not data.get("mobile_friendly"):

        score += 15

        reasons.append(
            "Poor mobile optimization"
        )

        opportunities.append(
            "Mobile responsive redesign"
        )

    # ========================================================
    # BOOKING
    # ========================================================

    if not data.get("booking"):

        score += 10

        reasons.append(
            "No online booking detected"
        )

        opportunities.append(
            "Online booking system"
        )

    # ========================================================
    # WHATSAPP
    # ========================================================

    if not data.get("whatsapp"):

        score += 10

        reasons.append(
            "No WhatsApp integration detected"
        )

        opportunities.append(
            "WhatsApp automation"
        )

        opportunities.append(
            "AI WhatsApp chatbot"
        )

    # ========================================================
    # CONTACT FORM / EMAIL
    # ========================================================

    if not data.get("emails"):

        score += 5

        reasons.append(
            "No public business email detected"
        )

        opportunities.append(
            "Business email setup"
        )

    # ========================================================
    # PHONE
    # ========================================================

    if not data.get("phones"):

        score += 5

        reasons.append(
            "No public phone number detected"
        )

        opportunities.append(
            "Contact information improvement"
        )

    # ========================================================
    # INSTAGRAM
    # ========================================================

    if not data.get("instagram"):

        score += 5

        reasons.append(
            "Instagram not linked"
        )

        opportunities.append(
            "Instagram integration"
        )

    # ========================================================
    # FACEBOOK
    # ========================================================

    if not data.get("facebook"):

        score += 3

        reasons.append(
            "Facebook not linked"
        )

        opportunities.append(
            "Social media integration"
        )

    # ========================================================
    # LEAD TYPE
    # ========================================================

    if (
        data.get("website_status") == "broken"
        or not data.get("mobile_friendly")
    ):

        lead_type = "Website Redesign"

    elif not data.get("whatsapp"):

        lead_type = "WhatsApp Automation"

    elif not data.get("booking"):

        lead_type = "Online Booking"

    else:

        lead_type = "Digital Growth"

    # ========================================================
    # LIMIT SCORE
    # ========================================================

    score = min(score, 100)

    # ========================================================
    # PRIORITY
    # ========================================================

    if score >= 70:

        priority = "HOT"

    elif score >= 40:

        priority = "WARM"

    else:

        priority = "COLD"

    return (
        score,
        priority,
        reasons,
        opportunities,
        lead_type
    )