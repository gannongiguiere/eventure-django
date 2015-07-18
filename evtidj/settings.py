"""
Django settings for evtidj project.

Generated by 'django-admin startproject' using Django 1.8.

For more information on this file, see
https://docs.djangoproject.com/en/1.8/topics/settings/

For the full list of settings and their values, see
https://docs.djangoproject.com/en/1.8/ref/settings/
"""

# Build paths inside the project like this: os.path.join(BASE_DIR, ...)
import os
import socket
from six.moves.urllib.parse import quote
from kombu import Exchange, Queue

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/1.8/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = '+&6-94v)i$^^u*cngktky$iwfe=f&d(6a&t!8y)ig*oak(c_5s'

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

ALLOWED_HOSTS = []


AUTH_USER_MODEL = 'core.Account'

# Application definition

INSTALLED_APPS = (
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'djcelery',
    'debug_toolbar',
    'corsheaders',
    'rest_framework',
    'core',
    'fe',

    'django.contrib.gis',
)

MIDDLEWARE_CLASSES = (
    'django.contrib.sessions.middleware.SessionMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.auth.middleware.SessionAuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'django.middleware.security.SecurityMiddleware',
)

ROOT_URLCONF = 'evtidj.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
    {
        'BACKEND': 'django.template.backends.jinja2.Jinja2',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'environment': 'fe.jinja2env.environment'
        }
    }
]

WSGI_APPLICATION = 'evtidj.wsgi.application'


# Database
# https://docs.djangoproject.com/en/1.8/ref/settings/#databases

DATABASES = {
    'default': {
        'ENGINE': 'django.contrib.gis.db.backends.postgis',
        'NAME': 'evti',
        'USER': 'web-dev',
        'PASSWORD': '1Billion',
        'HOST': 'pgdb-dev.eventure.com',
        'PORT': 5432,
    }
}

SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

CORS_ORIGIN_ALLOW_ALL = True   # Ok for dev, tighten up in stage/prod
CORS_ALLOW_CREDENTIALS = True

# Internationalization
# https://docs.djangoproject.com/en/1.8/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_L10N = True

USE_TZ = True


# Email Config
EMAIL_HOST = "smtpout.secureserver.net"
EMAIL_PORT = 465
EMAIL_HOST_USER = "from.all.of.us@eventure.com"
EMAIL_HOST_PASSWORD = "1Billion"
EMAIL_USE_SSL = True
EMAIL_FROM = "from.all.of.us@eventure.com"

# SMS Config
SMS_API_KEY = 'ACf57523cb591698a250610618fa447227'
SMS_API_SECRET = '8a0027cfb4ba9e5810b6a575f4479410'
SMS_FROM = '+19495580070'  # This is a number managed in the twillo.com account (SMS service provider)

# Establish if we are in test mode; changed in evtidj.testrunner module
TEST_RUNNER = 'evtidj.testrunner.MyTestSuiteRunner'
IN_TEST_MODE = False

# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/1.8/howto/static-files/

STATIC_URL = '/static/'
STATIC_ROOT = '/var/local/eventure-api/static/'

# Goes in the email footers, and it is a pain to figure out what the fully qualified url is at that
# point. Punting with a setting.
REGISTER_URL = 'http://devapi.eventure.com:8000/e/create-account-email'

REST_FRAMEWORK = {
    'PAGE_SIZE': 25,
    'DEFAULT_FILTER_BACKENDS': ('rest_framework.filters.DjangoFilterBackend',)
}


LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': "[%(asctime)s] %(levelname)s [%(name)s:%(lineno)s] %(message)s",
            'datefmt': "%d/%b/%Y %H:%M:%S"
        },
        'simple': {
            'format': '%(levelname)s %(message)s'
        },
    },
    'handlers': {
        'file': {
            'level': 'DEBUG',
            'class': 'logging.handlers.TimedRotatingFileHandler',
            'when': 'midnight',
            'filename': 'logs/django.log',
            'formatter': 'verbose'
        },
        'log_file':{
            'level': 'DEBUG',
            'class': 'logging.handlers.TimedRotatingFileHandler',
            'when': 'midnight',
            'filename': 'logs/log.log',
            'formatter': 'verbose'
        },
    },
    'loggers': {
        'django': {
            'handlers': ['file'],
            'propagate': True,
            'level': 'DEBUG',
        },
        'core': {
            'handlers': ['log_file'],
            'level': 'DEBUG',
        },
    }
}

# These credentials are for the eventure-mediaserver-dev account
AWS_MEDIA_ACCESS_KEY = 'AKIAIUIZFAO5NV43556Q'
AWS_MEDIA_SECRET_KEY = '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'
S3_MEDIA_UPLOAD_BUCKET = 'evtimedia'
S3_MEDIA_KEY_PREFIX = 'dev/'
S3_MEDIA_REGION = 'us-east-1'


CELERY_RESULT_BACKEND = 'djcelery.backends.database:DatabaseBackend'
CELERY_TASK_SERIALIZER = 'json'
CELERY_ACCEPT_CONTENT = ['json', 'yaml']
CELERY_RESULT_SERIALIZER = 'json'

CELERY_ENABLE_REMOTE_CONTROL = False
CELERY_SEND_EVENTS = False

CELERY_ENABLE_UTC = True
CELERY_DISABLE_RATE_LIMITS = True
BROKER_URL = 'sqs://{}:{}@'.format(AWS_MEDIA_ACCESS_KEY, quote(AWS_MEDIA_SECRET_KEY, safe=''))
BROKER_TRANSPORT_OPTIONS = {
    'queue_name_prefix': 'dev-',
    'visibility_timeout': 60,  # seconds
    'wait_time_seconds': 20,   # Long-polling
}

HOST_NAME = socket.gethostname()

# EOF
