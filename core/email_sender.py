from django.conf import settings
from django.core.mail import EmailMultiAlternatives, EmailMessage
from django.core import mail
from django.template import Context
from django.template.loader import get_template
from core.shared.const.choice_types import NotificationTypes
from django.contrib.contenttypes.models import ContentType
from core.models import Account, CommChannel
from celery import shared_task
from django.utils import timezone
import pytz
import logging
logger = logging.getLogger(__name__)


# MAPPING from NotificationType to Email Template
notification_map = {
    NotificationTypes.EVENT_INVITE.value: 'email/plan-invitation',
    NotificationTypes.EVENT_CANCEL.value: 'email/event-cancelled',
    NotificationTypes.EVENT_UPDATE.value: 'email/event-changed',
    # NotificationTypes.EVENTGUEST_RSVP.value: '',  TODO
    # NotificationTypes.ALBUMFILE_UPLOAD.value: '',  TODO
    NotificationTypes.ACCOUNT_EMAIL_VALIDATE.value: 'email/activate-email',
}


def get_template_subject(rendered_template):
    """Remove <subject>Subject</subject> from rendered template.

    Returns a tuple containing the rendered_template ex subject and the subject_text.

    E.g. "Hello <subject>Greetings</subject>there."
    Returns:
    ("Hello there.", "Greetings")
    """

    # find the subject in the rendered html
    subject_start = str.find(rendered_template, "<subject>")
    subject_end = str.find(rendered_template, "</subject>")

    if subject_start == -1 or subject_end == -1:
        raise ValueError("Subject not found")

    subject = rendered_template[subject_start + 9: subject_end]
    out = rendered_template.replace("<subject>" + subject + "</subject>", "")

    return (out, subject)


def _send(NotificationType, to_email, data):
    template = notification_map.get(NotificationType)
    if template is None:
        raise NotImplementedError("Email template for NotificationType %s is not found" % (NotificationType))

    plaintext = get_template(template + '.txt')
    htmly = get_template(template + '.htm')

    ctx = Context(data)
    html_content = htmly.render(ctx)

    html_content, subject = get_template_subject(html_content)
    text_content = plaintext.render(ctx)

    msg = EmailMultiAlternatives(subject, text_content, settings.EMAIL_FROM, [to_email])
    msg.attach_alternative(html_content, "text/html")
    msg.send()

    logger.info("Sending email [" + str(NotificationType) + "] to [" + str(to_email) + "] sucessful")


@shared_task
def send_email(NotificationType, sender_id, recipient_id, obj_model_class, obj_id, **kwargs):
    # TODO Check if recipient wants email notification for this notification type

    # Gather email data then send
    recipient = Account.objects.get(pk=recipient_id)
    to_email = recipient.email

    if to_email:
        data = gather_email_data(NotificationType, sender_id, recipient_id, obj_model_class, obj_id, **kwargs)
        _send(NotificationType, to_email, data)


def gather_email_data(NotificationType, sender_id, recipient_id, obj_model_class, obj_id, **kwargs):
    ''' Each email notification template requires certain fields '''
    if sender_id:
        sender = Account.objects.get(pk=sender_id)

    recipient = Account.objects.get(pk=recipient_id)
    content_type = ContentType.objects.get(app_label=Account._meta.app_label, model=obj_model_class)
    content_object = content_type.get_object_for_this_type(pk=obj_id)

    data = {
        'Site_Url': settings.SITE_URL,
        'to_email': recipient.email,
        "RegisterUrl": settings.REGISTER_URL,
    }

    if NotificationType in {NotificationTypes.EVENT_INVITE.value, NotificationTypes.EVENT_UPDATE.value}:
        timezone = pytz.timezone(content_object.timezone)
        start_dt = content_object.start.astimezone(timezone)
        data.update({
            'AccountName': sender.name,
            'Title': content_object.title,
            'StartDate': start_dt.strftime("%B {day}, %Y at {hour}:%M (%Z)").format(
                day=start_dt.day, hour=start_dt.strftime("%I").lstrip('0')),
            'Address': content_object.location,
            'Phone': sender.phone,
            'Notes': None,
            'PlanID': obj_id,
            'RSVPUrl': kwargs['rsvp_url'],
            'HostProfileUrl': kwargs['host_profile_url'],
        })
    elif NotificationType == NotificationTypes.EVENT_CANCEL.value:
        event = content_object
        data.update({
            'Title': event.title,
            'EventCancelledURL': kwargs['event_cancelled_url'],
        })
    # elif NotificationType == NotificationTypes.EVENTGUEST_RSVP.value:  TODO
    # elif NotificationType == NotificationTypes.ALBUMFILE_UPLOAD.value:  TODO

    return data


@shared_task
def async_send_validation_email(commchannel_id, validation_url):

    try:
        comm_channel = CommChannel.objects.get(pk=commchannel_id)
    except CommChannel.DoesNotExist:
        raise ValueError('No object with ID %s is found in core_commchannel' % (commchannel_id))

    to_email = comm_channel.comm_endpoint
    token = comm_channel.validation_token
    data = {'Site_Url': settings.SITE_URL,
            'ActivationUrl': validation_url,
            'Email': to_email,
            'RegisterUrl': settings.REGISTER_URL,
            }
    _send(NotificationTypes.ACCOUNT_EMAIL_VALIDATE.value, to_email, data)

    comm_channel.message_sent_date = timezone.now()
    comm_channel.save()


def send_validation_email(account_id, email, validation_url_fn):
        comm_channel = CommChannel.objects.create(account_id=account_id,
                                                  comm_type=CommChannel.EMAIL,
                                                  comm_endpoint=email)
        validation_url = validation_url_fn(comm_channel.validation_token)
        async_send_validation_email(comm_channel.id, validation_url)
