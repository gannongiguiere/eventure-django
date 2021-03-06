from datetime import datetime, timedelta
from six.moves.urllib.parse import unquote
import uuid
import boto
import phonenumbers
from phonenumbers.phonenumberutil import NumberParseException
import mimetypes
import hashlib
from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.core.validators import RegexValidator, MinValueValidator, MaxValueValidator, EmailValidator
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin, BaseUserManager
from django.contrib.postgres.fields import HStoreField
from django.utils import timezone
import pytz
from django.utils.translation import ugettext_lazy as _
from django.conf import settings
from django.core.exceptions import ValidationError
from django.contrib.gis.db import models
from django.contrib.gis.geos import Point
from core.shared.const.choice_types import NotificationTypes, CalendarTypes, EventStatus
from jsonfield import JSONField
from rest_framework import serializers
from core.modelfields import EmptyStringToNoneField
from core.validators import validate_phone_number
import oauth2client
import base64
import pickle
import logging
logger = logging.getLogger(__name__)


class AccountUserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, phone_country=None, phone=None, is_staff=False,
                     is_superuser=False, status=None, **extra):
        if phone:
            phone = self.model.normalize_phone(phone, phone_country)
        if email:
            email = self.normalize_email(email)

        if not (email or phone):
            raise ValueError('An email or phone is required to create this user')
        if not password:
            raise ValueError('Password cannot be empty')
        user = self.model(email=email, phone=phone, is_staff=is_staff, is_superuser=is_superuser, **extra)

        if status is not None:
            user.status = status
        user.set_password(password)
        user.save(self._db)
        return user

    def create_user(self, email, password, phone_country=None, phone=None, **extra):
        return self._create_user(email, password, phone_country, phone, False, False, **extra)

    def create_superuser(self, email, password, phone_country=None, phone=None, **extra):
        return self._create_user(email, password, phone_country, phone, True, True, AccountStatus.ACTIVE, **extra)


class AccountStatus:
    CONTACT = -1  # Not signed up; stub account for future account
    SIGNED_UP = 0
    DELETED = 2
    ACTIVE = 3
    DEACTIVE_FORCEFULLY = 5


class ActiveAccountUserManager(models.Manager):

    def get_queryset(self):
        return super().get_queryset().filter(status=AccountStatus.ACTIVE)


class Account(AbstractBaseUser, PermissionsMixin):

    class Meta:
        ordering = ('email',)

    STATUS_CHOICES = (
        (AccountStatus.CONTACT, 'Contact'),
        (AccountStatus.SIGNED_UP, 'Signed Up'),
        (AccountStatus.DELETED, 'Deleted'),
        (AccountStatus.ACTIVE, 'Active'),
        (AccountStatus.DEACTIVE_FORCEFULLY, 'Forcefully Inactivated'),
    )

    PUBLIC = 0
    PRIVATE = 2

    PRIVACY_CHOICES = (
        (PUBLIC, 'Public'),
        (PRIVATE, 'Private'),
    )

    email = EmptyStringToNoneField(unique=True, max_length=100, null=True, validators=[EmailValidator()])
    phone = EmptyStringToNoneField(unique=True, max_length=40, null=True, blank=True,
                                   validators=[validate_phone_number])
    name = models.CharField(max_length=255, blank=True)
    status = models.SmallIntegerField(choices=STATUS_CHOICES, default=AccountStatus.SIGNED_UP)

    show_welcome_page = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    last_ntf_checked = models.DateTimeField(null=True)
    profile_privacy = models.PositiveSmallIntegerField(choices=PRIVACY_CHOICES, default=PUBLIC)
    profile_albumfile = models.ForeignKey('AlbumFile', blank=True, null=True)
    solr_id = EmptyStringToNoneField(unique=True, max_length=45, null=True, blank=True)
    created = models.DateTimeField(default=timezone.now)
    modified = models.DateTimeField(auto_now=True)
    date_joined = models.DateTimeField(null=True)

    objects = AccountUserManager()
    actives = ActiveAccountUserManager()

    @property
    def is_active(self):
        return self.status in {AccountStatus.ACTIVE, AccountStatus.SIGNED_UP}

    def get_full_name(self):
        return self.name

    def get_short_name(self):

        return self.name

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    @classmethod
    def normalize_phone(cls, phone_number, country=None):
        """Return the canonical phone number for a given phone number and country.

        Assumes a US phone number if no country is given.
        """
        errmsg = _('Does not seem to be a valid phone number')
        if len(phone_number) < 1:
            return ValueError(_("Phone number too short."))
        if not country and phone_number[0] != '+':
            # Assume US phone number
            country = 'US'

        try:
            pn = phonenumbers.parse(phone_number, country)
        except NumberParseException:
            pn = cls._try_plus_phonenumber(phone_number, errmsg)

        if not phonenumbers.is_valid_number(pn):
            # Sometimes a foreign number can look like a valid US number, but it's not
            # e.g. 48794987216 - Parses ok, but not is_valid_number. Adding + fixes.
            pn = cls._try_plus_phonenumber(phone_number, errmsg)

            if not phonenumbers.is_valid_number(pn):
                raise ValueError(errmsg)

        return phonenumbers.format_number(pn, phonenumbers.PhoneNumberFormat.E164)

    @staticmethod
    def _try_plus_phonenumber(phone_number, errmsg):
        "Try adding + to a phone number to make it valid."
        if phone_number[0] != '+':
            try:
                return phonenumbers.parse("+{}".format(phone_number), None)
            except NumberParseException:
                raise ValueError(errmsg)
        else:
            raise ValueError(errmsg)

    def __str__(self):
        return self.get_short_name()


class AppleTokens():
    def __init__(self, **kwargs):
        self.apple_id = kwargs.get('apple_id')
        self.apple_password = kwargs.get('apple_password')  # app specific password
        self.x_apple_webauth_user = kwargs.get('x_apple_webauth_user')
        self.x_apple_webauth_token = kwargs.get('x_apple_webauth_token')


class MyCredentialsField(models.Field):
    def __init__(self, *args, **kwargs):
        if 'null' not in kwargs:
            kwargs['null'] = True
        super().__init__(*args, **kwargs)

    def get_internal_type(self):
        return "TextField"

    def to_python(self, value):
        if value is None:
            return None
        if isinstance(value, oauth2client.client.Credentials) or isinstance(value, AppleTokens):
            return value

        value = value.encode("utf-8")  # string to byte

        return pickle.loads(base64.b64decode(value))

    def from_db_value(self, value, expression, connection, context):
        return self.to_python(value)

    def get_db_prep_value(self, value, connection, prepared=False):
        if value is None:
            return None

        byte_repr = base64.b64encode(pickle.dumps(value))

        return byte_repr.decode("utf-8")  # byte to string

    def get_prep_value(self, value):
        return self.get_db_prep_value(value)


class GoogleCredentials(models.Model):
    account = models.OneToOneField(Account, primary_key=True, )
    credentials = MyCredentialsField()


class AppleCredentials(models.Model):
    account = models.OneToOneField(Account, primary_key=True, related_name='apple_credentials')
    credentials = MyCredentialsField()


class EventPrivacy(object):
    "Helper to define Event Privacy enums (used in Account Settings and Events)."

    PUBLIC = 1
    PRIVATE = 2

    PRIVACY_CHOICES = (
        (PUBLIC, 'Public'),
        (PRIVATE, 'Private'),
    )


class AccountSettings(models.Model):

    class Meta:
        verbose_name_plural = "account settings"

    _color_validator = RegexValidator(r"[A-Fa-f0-9]{6}", message="Not a valid color (needs to be in hex format, e.g. FE00AC)")

    account = models.OneToOneField(Account, primary_key=True)
    email_rsvp_updates = models.BooleanField(default=True)
    email_social_activity = models.BooleanField(default=True)
    email_promotions = models.BooleanField(default=True)
    text_rsvp_updates = models.NullBooleanField()
    text_social_activity = models.NullBooleanField()
    text_promotions = models.NullBooleanField()
    default_event_privacy = models.PositiveSmallIntegerField(choices=EventPrivacy.PRIVACY_CHOICES, default=EventPrivacy.PRIVATE)
    work_calendar_color = models.CharField(max_length=6, default="FF7979", validators=[_color_validator])
    home_calendar_color = models.CharField(max_length=6, default="00AEE3", validators=[_color_validator])
    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)

    def __str__(self):
        return "AccountSettings {}".format(self.account_id)


class AlbumType(models.Model):

    class Meta:
        ordering = ('sort_order',)

    id = models.PositiveIntegerField(primary_key=True)  # No auto-increment
    name = models.CharField(unique=True, max_length=40)
    description = models.CharField(max_length=80)
    sort_order = models.PositiveSmallIntegerField()
    is_virtual = models.BooleanField()
    is_deletable = models.BooleanField()
    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

# Contains name as key and AlbumType object as value
ALBUM_TYPE_MAP = dict((at.name, at) for at in AlbumType.objects.all())


class ActiveStatusManager(models.Manager):
    "Return only items where the status is ACTIVE."

    def get_queryset(self):
        return super(ActiveStatusManager, self).get_queryset().filter(status=self.model.ACTIVE)


class ActiveProcessingStatusManager(models.Manager):
    "Return only items where the status is ACTIVE or PROCESSING"

    def get_queryset(self):
        return super(ActiveStatusManager, self).get_queryset()\
            .filter(status__in=(self.model.ACTIVE, self.model.PROCESSING))


class AlbumFile(models.Model):

    PHOTO_TYPE = 1
    VIDEO_TYPE = 2

    FILETYPE_CHOICES = (
        (PHOTO_TYPE, 'PHOTO'),
        (VIDEO_TYPE, 'VIDEO'),
    )

    ACTIVE = 1
    INACTIVE = 2
    DELETED = 3
    PROCESSING = 4
    ERROR = 5

    STATUS_CHOICES = (
        (ACTIVE, 'Active'),
        (INACTIVE, 'Inactive'),
        (PROCESSING, 'Processing'),
        (ERROR, 'Error'),
        (DELETED, 'Deleted'),
    )

    class Meta:
        unique_together = (("s3_bucket", "s3_key"),)

    objects = models.Manager()
    active = ActiveStatusManager()
    activepending = ActiveProcessingStatusManager()

    owner = models.ForeignKey('Account')
    name = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)
    file_url = models.URLField(unique=True, null=True)
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    size_bytes = models.PositiveIntegerField()
    file_type = models.PositiveSmallIntegerField(choices=FILETYPE_CHOICES)
    status = models.SmallIntegerField(choices=STATUS_CHOICES)
    albums = models.ManyToManyField('Album', related_name='albumfiles')

    s3_bucket = models.CharField(max_length=255, null=True)
    s3_key = models.CharField(max_length=255, null=True)

    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)
    media_created = models.DateTimeField(null=True, blank=True)  # When the base media file was taken/created.

    def __str__(self):
        return self.name

    def upload_s3_photo(self, file_obj, img_format):
        "Upload a photo contained in file_obj to s3 and set the appropriate albumfile properties."

        if self.file_type == AlbumFile.VIDEO_TYPE:
            raise NotImplementedError('Videos are unsupported')

        self.s3_bucket = settings.S3_MEDIA_UPLOAD_BUCKET
        conn = boto.s3.connect_to_region(settings.S3_MEDIA_REGION,
                                         aws_access_key_id=settings.AWS_MEDIA_ACCESS_KEY,
                                         aws_secret_access_key=settings.AWS_MEDIA_SECRET_KEY)

        bucket = conn.get_bucket(self.s3_bucket, validate=False)

        if not self.s3_key:
            datepart = datetime.utcnow().strftime("%Y/%m/%d")
            fname = uuid.uuid4()
            fmtargs = dict(datepart=datepart, filename=fname, ext=img_format.lower(),
                           prefix=settings.S3_MEDIA_KEY_PREFIX)
            self.s3_key = "{prefix}img/{datepart}/{filename}.{ext}".format(**fmtargs)
            k = bucket.new_key(self.s3_key)
        else:
            k = bucket.get_key(self.s3_key)

        headers = {}
        content_type = mimetypes.types_map.get('.' + img_format.lower())
        if content_type:
            headers['Content-Type'] = content_type

        file_obj.seek(0)
        self.size_bytes = k.set_contents_from_file(file_obj, headers=headers, policy='public-read')
        self.file_url = k.generate_url(expires_in=0, query_auth=False)


class Album(models.Model):

    ACTIVE = 1
    INACTIVE = 2
    DELETED = 3

    STATUS_CHOICES = (
        (ACTIVE, 'Active'),
        (INACTIVE, 'Inactive'),
        (DELETED, 'Deleted'),
    )

    class Meta:
        ordering = ('album_type__sort_order',)

    objects = models.Manager()
    active = ActiveStatusManager()

    owner = models.ForeignKey('Account', related_name='albums')  # This is the owner of the album
    event = models.ForeignKey('Event', related_name='albums', null=True, blank=True, default=None)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    album_type = models.ForeignKey('AlbumType')
    status = models.SmallIntegerField(choices=STATUS_CHOICES, default=ACTIVE)
    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

    def albumfiles_queryset(self, account):
        """Return an appropriate queryset for this albumfile type.

        Queryset will vary depending on if the album_type is virtual.
        """
        if not self.album_type.is_virtual:
            return self.albumfiles.filter(status=AlbumFile.ACTIVE)
        elif self.album_type.name == "ALLMEDIA":
            return AlbumFile.active.filter(owner=account)
        else:
            raise NotImplementedError(_("%(name)s albumfiles query not implemented") % {'name': self.album_type.name})


class Thumbnail(models.Model):

    class Meta:
        ordering = ('size_type',)
        unique_together = (('albumfile', 'size_type'))

    SIZE_48 = 48
    SIZE_100 = 100
    SIZE_144 = 144
    SIZE_205 = 205
    SIZE_320 = 320
    SIZE_610 = 610
    SIZE_960 = 960

    SIZE_CHOICES = (
        (SIZE_48, "SIZE_48"),
        (SIZE_100, "SIZE_100"),
        (SIZE_144, "SIZE_144"),
        (SIZE_205, "SIZE_205"),
        (SIZE_320, "SIZE_320"),
        (SIZE_610, "SIZE_610"),
        (SIZE_960, "SIZE_960"),
    )

    albumfile = models.ForeignKey('AlbumFile', related_name='thumbnails')
    file_url = models.URLField(unique=True)
    size_type = models.PositiveSmallIntegerField(choices=SIZE_CHOICES)
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    size_bytes = models.PositiveIntegerField()
    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)

    @property
    def name(self):
        if self.file_url:
            return unquote(self.file_url.split("/")[-1])
        else:
            return ""

    def __str__(self):
        return self.name


class Event(models.Model):

    PRIVATE = EventPrivacy.PRIVATE
    PUBLIC = EventPrivacy.PUBLIC

    title = models.CharField(max_length=100,)
    start = models.DateTimeField()
    end = models.DateTimeField()
    timezone = models.CharField(max_length=40, choices=[(tz, tz) for tz in pytz.common_timezones])
    owner = models.ForeignKey('Account', related_name='events')
    guests = models.ManyToManyField('Account', through='EventGuest')

    privacy = models.SmallIntegerField(choices=EventPrivacy.PRIVACY_CHOICES, default=PUBLIC)
    calendar_type = models.PositiveSmallIntegerField(choices=CalendarTypes.choices(),
                                                     default=CalendarTypes.PERSONAL_CALENDAR.value)
    status = models.PositiveSmallIntegerField(choices=EventStatus.choices(),
                                              default=EventStatus.DRAFT.value)

    location = models.CharField(max_length=250, null=True)
    lon = models.FloatField(null=True)
    lat = models.FloatField(null=True)
    mpoint = models.PointField(null=True, geography=True)
    is_all_day = models.BooleanField(default=False)
    featured_albumfile = models.ForeignKey('AlbumFile', blank=True, null=True)
    comments = GenericRelation('Comment')

    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)

    objects = models.GeoManager()

    # We will use a signal to store previous values for these fields. Will help us to determine
    # if we should send notifications out on the event change.
    tracked_fields = ('start', 'end', 'timezone', 'privacy', 'status', 'location', 'lat', 'lon', 'is_all_day')

    class Meta:
        ordering = ('created',)

    def __str__(self):
        return "%s %s" % (self.id, self.title)

    def save(self, *args, **kwargs):

        if self.lon is not None and self.lat is not None:
            self.mpoint = Point(self.lon, self.lat, srid=4326)

        super(Event, self).save(*args, **kwargs)


class EventGuest(models.Model):
    UNDECIDED = 0
    YES = 1
    NO  = 2
    MAYBE = 3

    RSVP_CHOICES = (
        (UNDECIDED, 'Undecided'),
        (YES, 'Yes'),
        (NO, 'No'),
        (MAYBE, 'Maybe')
    )

    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)

    guest = models.ForeignKey('Account', related_name='guests')
    name = models.CharField(max_length=255, blank=True)
    event = models.ForeignKey('Event')
    rsvp = models.SmallIntegerField(choices=RSVP_CHOICES, default=UNDECIDED)
    token = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    tracked_fields = ('rsvp',)

    # class Meta:
    #     This unique_together constraint is in the db (see migration core 0062_manual_eventguest_unique_constraint)
    #     but causes problems in the rest api, so it's commented out of the model.
    #     unique_together = (("guest", "event"),)


class InAppNotification(models.Model):
    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)

    sender = models.ForeignKey('Account', related_name='sent_ntfs')
    recipient = models.ForeignKey('Account', related_name='received_ntfs')
    notification_type = models.SmallIntegerField(choices=NotificationTypes.choices())

    #polymorphic generic relation (ForeignKey to multiple models)
    content_type = models.ForeignKey(ContentType)
    object_id = models.PositiveIntegerField()
    content_object = GenericForeignKey('content_type', 'object_id')


class Follow(models.Model):
    PENDING = 0
    APPROVED = 1
    UNAPPROVED = 2

    STATUS_CHOICES = (
        (PENDING, 'PENDING'),
        (APPROVED, 'APPROVED'),
        (UNAPPROVED, 'UNAPPROVED')
    )

    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)

    follower = models.ForeignKey('Account', related_name='followings')
    followee = models.ForeignKey('Account', related_name='followers')
    status = models.SmallIntegerField(choices=STATUS_CHOICES, default=PENDING)


class CommChannel(models.Model):
    ''' Store info about validation of email or phone of account '''
    EMAIL = 0
    PHONE = 1

    COMM_CHANNEL_CHOICES = (
        (EMAIL, 'EMAIL'),
        (PHONE, 'PHONE'),
    )

    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)

    account = models.ForeignKey('Account', )
    comm_type = models.SmallIntegerField(choices=COMM_CHANNEL_CHOICES)
    comm_endpoint = models.CharField(max_length=100)  # email or phone to be validated
    validation_token = models.UUIDField(unique=True, default=uuid.uuid4)
    validation_date = models.DateTimeField(null=True)  # null if not yet validated
    message_sent_date = models.DateTimeField(null=True)


class PasswordReset(models.Model):
    "Store information about a password reset request."

    account = models.ForeignKey('Account')
    email = models.EmailField(db_index=True)
    token_salt = models.UUIDField(default=uuid.uuid4)
    message_sent_date = models.DateTimeField(null=True)
    reset_date = models.DateTimeField(null=True)
    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)

    # After this much time, the reset request is no longer valid.
    TOKEN_EXPIRY_TIMEDELTA = timedelta(days=1)

    def get_password_reset_token(self):
        """Calculate password reset token from inputs.

        Using a calculated field as we don't want to store plaintext tokens (similar
        to how we don't want to store cleartext passwords in the database). Probably not
        as bad a security risk as cleartext passwords (these are time limited tokens), but it's
        easy to implement...

        See also http://django-password-reset.readthedocs.org/en/latest/quickstart.html#what-you-get
        , which does something similar.
        """
        h = hashlib.new('sha256')
        h.update(self.message_sent_date.isoformat().encode('utf8'))
        h.update(str(self.token_salt).encode('utf8'))
        h.update(self.account.password.encode('utf8'))
        h.update(settings.SECRET_KEY.encode('utf8'))
        if self.account.last_login:
            h.update(self.account.last_login.isoformat().encode('utf8'))
        return h.hexdigest()

    def can_still_use(self):
        """Return True we are still a valid reset object.

        Tests that the token hasn't expired, and that it hasn't been used.
        """
        cutoff = timezone.now() - self.TOKEN_EXPIRY_TIMEDELTA
        return (self.reset_date is None) and (self.message_sent_date > cutoff)

    def update_password(self, new_password):
        """Update the user password and mark the token as used.

        Performs a 'save' on both this object and the Account.
        """
        self.account.set_password(new_password)
        self.reset_date = timezone.now()
        self.save()
        self.account.save()


class Comment(models.Model):
    "Comment on something."

    owner = models.ForeignKey('Account')
    parent = models.ForeignKey('Comment', null=True, blank=True, related_name='responses')
    text = models.TextField()

    content_type = models.ForeignKey(ContentType)
    object_id = models.PositiveIntegerField()
    content_object = GenericForeignKey('content_type', 'object_id')

    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.text
