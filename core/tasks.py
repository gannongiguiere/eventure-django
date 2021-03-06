"Module for async celery tasks."

from datetime import datetime, timedelta
import json
import logging
from celery import shared_task, chord
from django.conf import settings
from PIL import Image
import uuid
from core.models import (
    AlbumFile, Thumbnail, InAppNotification, Event, Account, AccountSettings, AccountStatus, PasswordReset,
    EventGuest)
from core.shared.const.choice_types import NotificationTypes
from core.shared.utilities import get_absolute_url
from core.email_sender import send_email, get_template_subject
from core.sms_sender import send_sms
from django.core.mail import send_mail
from django.contrib.contenttypes.models import ContentType
from django.template import Context
from django.template.loader import get_template
from django.utils import timezone

logger = logging.getLogger('core.tasks')


################### NOTIFICATIONS ##################
def async_send_notifications(notification_type, sender_id, recipient_id, obj_model_class, obj_id):
    "Send out notifications: email, inapp, push, sms"

    send_inapp_notification.s(notification_type, sender_id, recipient_id, obj_model_class, obj_id).delay()

    if is_email_ntf_allowed(notification_type, recipient_id):
        send_email.s(notification_type, sender_id, recipient_id, obj_model_class, obj_id).delay()


def is_email_ntf_allowed(notification_type, recipient_id):
    if not Account.objects.filter(pk=recipient_id, status=AccountStatus.ACTIVE, email__isnull=False).exists():
        return False
    if notification_type == NotificationTypes.EVENTGUEST_RSVP.value:
        return AccountSettings.objects.filter(account_id=recipient_id, email_rsvp_updates=True).exists()
    else:
        return AccountSettings.objects.filter(account_id=recipient_id, email_social_activity=True).exists()


@shared_task
def send_inapp_notification(notification_type, sender_id, recipient_id, obj_model_class, obj_id):
    content_type = ContentType.objects.get(app_label=AlbumFile._meta.app_label, model=obj_model_class)
    content_object = content_type.get_object_for_this_type(pk=obj_id)
    ntf = InAppNotification.objects.create(notification_type=notification_type, sender_id=sender_id, recipient_id=recipient_id, content_object=content_object)
    ntf.save()


@shared_task
def send_event_invitations(event_id, guest_account_id=None):

    logger.info('Sending invitiations for event_id: {}'.format(event_id))
    event = Event.objects.get(pk=event_id)
    ntype = NotificationTypes.EVENT_INVITE.value
    host_profile_url = get_absolute_url('fe:profile-external', kwargs={'account_id': event.owner.id})

    if guest_account_id:
        guest_q = event.guests.filter(pk=guest_account_id)
    else:
        guest_q = event.guests.all()

    for account in guest_q:
        rsvp_url = _get_rsvp_url(event, account)

        # Prefer email over SMS
        if account.email:
            send_email(ntype, event.owner.id, account.id, 'event', event.id, rsvp_url=rsvp_url,
                       host_profile_url=host_profile_url)
        elif account.phone:
            if event.owner.name:
                msg = "{} has invited you to an event.\n".format(event.owner.name)
            else:
                msg = "You have been invited to an event.\n"

            msg += rsvp_url

            send_sms(account.phone, msg)


def _get_rsvp_url(event, guest_account):
    "Get the RSVP url for an event, including token (if necessary)."
    url = get_absolute_url('fe:event-rsvp', kwargs={'event_id': event.id})
    if guest_account.status != AccountStatus.ACTIVE:
        event_guest = EventGuest.objects.get(event=event, guest=guest_account)
        url += "t={}".format(event_guest.token)

    return url


@shared_task
def send_event_cancellation_notifications(event_id):

    logger.info('Sending cancellations for event {}'.format(event_id))
    event = Event.objects.get(pk=event_id)
    ntype = NotificationTypes.EVENT_CANCEL.value

    url = get_absolute_url('fe:event-cancelled', kwargs={'event_id': event_id})

    for account in event.guests.all():
        if account.email:
            send_email(ntype, event.owner.id, account.id, 'event', event.id, event_cancelled_url=url)
        elif account.phone:
            if event.owner.name:
                msg = "{} has cancelled an event.\n{}".format(event.owner.name, url)
            else:
                msg = 'The event "{}" has been cancelled.\n{}'.format(event.title, url)
            send_sms(account.phone, msg)


@shared_task
def send_event_update_notifications(event_id):

    logger.info('Sending cancellations for event {}'.format(event_id))
    event = Event.objects.get(pk=event_id)
    ntype = NotificationTypes.EVENT_UPDATE.value
    host_profile_url = get_absolute_url('fe:profile-external', kwargs={'account_id': event.owner.id})

    for account in event.guests.all():
        rsvp_url = _get_rsvp_url(event, account)
        if account.email:
            send_email(ntype, event.owner.id, account.id, 'event', event.id, rsvp_url=rsvp_url,
                       host_profile_url=host_profile_url)
        elif account.phone:
            if event.owner.name:
                msg = "{} has changed an event you are invited to.\n{}".format(event.owner.name, rsvp_url)
            else:
                msg = "An event you are invited to has changed.\n{}".format(rsvp_url)
            send_sms(account.phone, msg)


############### ALBUMFILE PROCESSING ##############

@shared_task
def finalize_s3_thumbnails(json_data):
    """Store s3 thubmnail information from AWS lambda into the DB.


    This function isn't actully called from Django, but a message is insereted
    by AWS lambda into the message queue and then executed by celery.
    """

    data = json.loads(json_data)

    logger.info('Got json_data %s', json_data)

    bucket_name = data.get('srcBucket')
    key_name = data.get('srcKey')

    try:
        albumfile = AlbumFile.objects.get(s3_bucket=bucket_name, s3_key=key_name)
    except AlbumFile.DoesNotExist:
        logger.error('AlbumFile not found for bucket: %r key: %r', bucket_name, key_name)
        return

    thumb_results = data.get('thumbnailResults')
    if not thumb_results:
        logger.error('Got no thumbnail results with key: %s, json: %s', key_name, json_data)
        return

    exist_thumb = dict((str(t.size_type), t) for t in albumfile.thumbnails.all())

    logger.info('exist_thumb %s', exist_thumb)

    for size, new_data in thumb_results.items():
        logger.info('Looking for size %r', size)
        thumb = exist_thumb.get(size) or Thumbnail()

        logger.info('Using thumb %r', thumb)

        bucket_name = new_data['Bucket']
        key_name = new_data['Key']

        thumb.file_url = new_data['Url']

        thumb.size_type = size
        thumb.width = new_data.get('Width', 0)
        thumb.height = new_data.get('Height', 0)
        thumb.size_bytes = new_data.get('SizeBytes', 0)
        thumb.albumfile_id = albumfile.id
        thumb.save()

    if albumfile.status == AlbumFile.PROCESSING:
        albumfile.status = AlbumFile.ACTIVE
        albumfile.save()


@shared_task
def send_password_reset_email(email_address, url_template):
    "Send password reset email and save sent data in the PasswordReset table."

    email = Account.objects.normalize_email(email_address)
    try:
        account = Account.objects.get(email=email, status__in=(AccountStatus.ACTIVE, AccountStatus.DELETED))
    except Account.DoesNotExist:
        logger.info("send_password_reset_email: No account exists for email {}".format(email))
        return False

    # See if we've sent an email in the last 5 minutes. If we have, let's do nothing.
    cutoff_dt = timezone.now() - timedelta(minutes=5)
    reset_count = PasswordReset.objects.filter(account=account,
                                               message_sent_date__gt=cutoff_dt,
                                               reset_date=None).count()
    if reset_count:
        logger.info("send_password_reset_email: Reset email has recently been sent for this account.")
        return False

    template = get_template('email/password_reset.htm')
    txttemplate = get_template("email/password_reset.txt")
    pwreset = PasswordReset(account=account, email=email, token_salt=uuid.uuid4(), message_sent_date=timezone.now())
    token = pwreset.get_password_reset_token()
    pwreset.save()
    context = Context({
        'reset_url': url_template.format(pw_reset_id=pwreset.id, token=token),
        'contact_email': settings.EMAIL_FROM,
    })
    htmlbody, subject = get_template_subject(template.render(context))
    txtbody = txttemplate.render(context)

    sent = send_mail(subject, txtbody, settings.EMAIL_FROM, [email_address], html_message=htmlbody)

    if sent == 1:
        return True
    else:
        return False
