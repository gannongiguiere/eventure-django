from django.core.urlresolvers import reverse
from rest_framework import status
from rest_framework.test import APITestCase, APITransactionTestCase

# from django.contrib.auth.models import User
from rest_framework.test import APIRequestFactory, APIClient
from core.models import Account, Album, AlbumType, AlbumFile, Thumbnail, Event, EventGuest
from django.utils import timezone
import datetime
from django.test.utils import override_settings
import time
import json
from core.tasks import finalize_s3_thumbnails


class EventTests(APITestCase):
    fixtures = ['core_initial_data.json']

    def setUp(self):
        # log in
        self.user = Account.objects.get(phone='+17146032364')
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

        self.user2 = Account.objects.get(phone='+17148885070')
        self.client2 = APIClient()
        self.client2.force_authenticate(user=self.user2)

    def test_create_event_start_date_in_the_future(self):
        '''
        Ensure event createion with start date in the past (compared to current UTC time) will fail
        '''
        url = reverse('event-list')
        data = {'title': 'Test Event 1',
            'start' : "2015-04-07T17:04:00Z",
            'end'   : "2015-04-07T17:04:00Z"}
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Start Date must not be in the past', response.data['start'])

    def test_create_event_end_date_later_than_start_date(self):
        '''
        Ensure if end date is ealier than start date, test will fail
        '''
        url = reverse('event-list')

        now = timezone.now()
        data = {'title': 'Test Event 2',
            'start' : now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            'end'   : (now - datetime.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")}

        response = self.client.post(url, data, format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @override_settings(CELERY_EAGER_PROPAGATES_EXCEPTIONS=True, CELERY_ALWAYS_EAGER=True,)
    def test_create_event_success_add_guest(self):
        '''
        Ensure test created successful
        '''
        # Create event
        url = reverse('event-list')

        now = timezone.now()
        data = {'title': 'Test Event 3',
                'start': (now + datetime.timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                'end': (now + datetime.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                'location': '3420 Bristol Street, Costa Mesa, CA 92626',
                }

        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        album_url = response.data['albums'][0]
        event_url = response.data['url']

        ''' Invite guest'''
        url = response.data['guests']
        data = {
            'guest': reverse('account-detail', kwargs={'pk': self.user2.id}),
        }
        response = self.client.post(url, data, format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        ''' Upload file to event album '''
        response = self.client.get(album_url)
        files_url = response.data['files']

        data = {
            'source_url': '''https://upload.wikimedia.org/wikipedia/commons/thumb/3/38/Shopping_Center_Magna_Plaza_Amsterdam_2014.jpg/1280px-Shopping_Center_Magna_Plaza_Amsterdam_2014.jpg''',
            'name': 'half dome 2',
        }
        response = self.client.post(files_url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        ''' Make sure file is uploaded and thumbnails proccessed. '''
        # NEED TO FIX CELERY PROBLEM: daemon's database is not test runner's database
        # response = self.client.get(files_url)
        # self.assertTrue(response.data['count'] > 0)

        # ALTENATELY: assume AWS lambda does it job, just check the celery thumbnail task

        last_af = AlbumFile.objects.latest('created')

        thumbnails_data = self.create_thumbnails_fixtures(last_af.s3_key, last_af.s3_bucket)
        finalize_s3_thumbnails.delay(json.dumps(thumbnails_data))

        ''' Make sure AlbumFile is done processing '''
        last_af = AlbumFile.objects.get(pk=last_af.id) # refresh the albumfile data
        self.assertEqual(last_af.status, AlbumFile.ACTIVE)

        ''' Make sure all thumbnails are saved '''
        self.assertTrue(Thumbnail.objects.filter(albumfile_id=last_af.id).count() == 7)

    def test_find_events(self):
        # Create event
        url = reverse('event-list')

        now = timezone.now()
        data = {'title': 'Event Crazy',
                'start': (now + datetime.timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                'end': (now + datetime.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                'location': '3420 Bristol Street, Costa Mesa, CA 92626',
                }

        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        event_url = response.data['url']

        ''' Find all events in a radius of 100 miles of a random location '''
        url = reverse('event-list')
        miles = '100'
        vicinity = 'costa mesa'
        url += '?miles=%s&vicinity=%s' % (miles, vicinity)

        response = self.client.get(url)
        found_events = list(filter(lambda ev: ev['url'] == event_url, response.data['results']))
        self.assertTrue(len(found_events) > 0)

        ''' Find events using title '''
        url = reverse('event-list') + "?title=Event Crazy"
        response = self.client.get(url)
        self.assertEqual(response.data['count'], 1)

    def create_thumbnails_fixtures(self, image_key, bucket):
        image_key = image_key.replace(".jpeg", "")
        data = {
            "srcKey": image_key + ".jpeg",
            "srcBucket": bucket,
            'thumbnailResults': {},
        }

        for size in ["48", "100", "144", "205", "320", "610", "960"]:
            thumbnail_data = {
                size: {
                    "Bucket": bucket + "-thumbnail",
                    "Key":  "%s_S%s.jpeg" % (image_key, size),
                    "SizeBytes": 1277,
                    "Width": int(size),
                    "Height": 31,
                    "Url": "https://%s-thumbnail.s3.amazonaws.com/%s_S%s.jpeg" % (bucket, image_key, size)
                }
            }
            data["thumbnailResults"].update(thumbnail_data)

        return data

# EOF
