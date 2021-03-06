from django.core.urlresolvers import reverse, resolve
from rest_framework import status
from rest_framework.test import APITestCase

# from django.contrib.auth.models import User
from rest_framework.test import APIClient
from core.models import Account, AlbumType, Event
from core.shared.const.choice_types import EventStatus
from django.utils import timezone
import datetime
from django.test.utils import override_settings
from django.utils.six.moves.urllib.parse import urlparse
from django.contrib.contenttypes.models import ContentType


class FollowTests(APITestCase):

    fixtures = ['core_accounts', ]

    def setUp(self):
        # self.create_fixtures()
        # log in
        self.user = Account.objects.get(email='huy.nguyen@eventure.com')
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

        self.user2 = Account.objects.get(email='tidushue@gmail.com')
        self.client2 = APIClient()
        self.client2.force_authenticate(user=self.user2)

    def create_event(self):
        url = reverse('event-list')

        now = timezone.now()
        data = {'title': 'Test Event Follow 1',
                'start': (now + datetime.timedelta(seconds=10)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                'end': (now + datetime.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                'timezone': 'America/Kentucky/Louisville',
                'status': EventStatus.ACTIVE.value}

        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        return response

    @override_settings(CELERY_EAGER_PROPAGATES_EXCEPTIONS=True,
                       CELERY_ALWAYS_EAGER=True,
                       # BROKER_BACKEND='memory'
                       )
    def test_notifications(self):
        # user creates event
        response = self.create_event()
        event_url = response.data['url']
        event_id = int(resolve(urlparse(event_url).path).kwargs['pk'])
        # user invites user2
        url = response.data['guests']
        data = {
            'guest': 'account_id:{}'.format(self.user2.id),
        }
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        # user2 must have event notification
        url = reverse('notification-list')
        response = self.client2.get(url)

        ntf = {}
        for n in response.data['results']:
            if n.get('content_type') == 'event':
                ntf = n
                break

        self.assertEqual(ntf.get('content_type'), 'event', ntf)
        self.assertEqual(ntf['notification_type'], 1)  # EVENT_INVITE
        self.assertEqual(ntf['object_id'], event_id, (ntf, event_url))

# EOF
