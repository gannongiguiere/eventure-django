from django.core.urlresolvers import reverse
from rest_framework import status
from rest_framework.test import APITestCase, APIClient
from core.models import Account, Album, AlbumType, AlbumFile, Thumbnail, Event, EventGuest
from core.shared.const.choice_types import EventStatus
from django.utils import timezone
import pytz
import datetime
from django.test.utils import override_settings
import time
import json
import re
from core.tasks import finalize_s3_thumbnails
from django.core import mail
from unittest.mock import patch


class EventTests(APITestCase):
    fixtures = ['core_initial_data_2']

    def setUp(self):
        # log in
        self.user = Account.objects.get(email='huy.nguyen@eventure.com')
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

        self.user2 = Account.objects.get(phone='+17148885070')
        self.client2 = APIClient()
        self.client2.force_authenticate(user=self.user2)

        self.user2.email = 'tidushue@gmail.com'
        self.user2.save()

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
                'start': (now + datetime.timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                'end': (now - datetime.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                'timezone': 'US/Eastern',
                }

        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('non_field_errors', response.data)
        self.assertEqual(response.data['non_field_errors'], ['End Date must be later than Start Date'])

        data = {'title': 'Test Event 2',
            'start' : (now - datetime.timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            'end'   : (now + datetime.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")}

        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['start'], ['Start Date must not be in the past'])

    @override_settings(CELERY_EAGER_PROPAGATES_EXCEPTIONS=True, CELERY_ALWAYS_EAGER=True,)
    def test_create_update_event_add_guest(self):
        '''
        Ensure test created successful
        '''
        # Create event
        url = reverse('event-list')

        now = timezone.now()
        event_title = 'Test Event for Create Update Event Add Guest'
        data = {'title': event_title,
                'start': (now + datetime.timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                'end': (now + datetime.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                'location': '3420 Bristol Street, Costa Mesa, CA 92626',
                'timezone': 'US/Pacific',
                'status': EventStatus.ACTIVE.value,
                }

        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        album_url = response.data['albums'][0]
        event_url = response.data['url']
        guests_url = response.data['guests']

        ''' Invite guest user2. user2 should have notifications'''
        url = response.data['guests']
        data = {
            'guest': "account_id:{}".format(self.user2.id),
        }
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

        ''' Check user2 has invite email '''
        invite_mail = mail.outbox[0]
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(invite_mail.subject, 'You have been invited to %s' % (event_title))
        self.assertIn(self.user2.email, invite_mail.to)
        # print(invite_mail.body)
        # print(invite_mail.alternatives)


        ''' Check guest list and first guest detail'''
        response = self.client.get(guests_url)
        eventguest_url = response.data['results'][0]['url']
        response = self.client.get(eventguest_url)
        self.assertEqual(response.data['guest'], 'http://testserver' + reverse('account-detail', kwargs={'pk': self.user2.id}))

        ''' Update guest reservation '''
        response = self.client.put(eventguest_url, {'rsvp': 2})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['rsvp'], 2)

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

        # ALTERNATIVE: assume AWS lambda does it job, just check the celery thumbnail task
        last_af = AlbumFile.objects.latest('created')

        thumbnails_data = self.create_thumbnails_fixtures(last_af.s3_key, last_af.s3_bucket)
        finalize_s3_thumbnails.delay(json.dumps(thumbnails_data))

        ''' Make sure AlbumFile is done processing '''
        last_af = AlbumFile.objects.get(pk=last_af.id) # refresh the albumfile data
        self.assertEqual(last_af.status, AlbumFile.ACTIVE)

        ''' Make sure all thumbnails are saved '''
        self.assertTrue(Thumbnail.objects.filter(albumfile_id=last_af.id).count() == 7)

        ''' Update event '''
        new_title = 'New title'
        new_start = (now + datetime.timedelta(minutes=5))

        fmt = "%Y-%m-%dT%H:%M:%SZ"

        response = self.client.patch(event_url, {
            'title': new_title,
            'start': new_start.strftime(fmt),
            'timezone': 'US/Pacific'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['title'], new_title)
        self.assertEqual(response.data['start'], new_start.replace(microsecond=0).strftime(fmt))  # why Django REST serialize DateTime without millisecond?

    def test_find_events(self):
        # Create event
        url = reverse('event-list')

        now = timezone.now()
        data = {'title': 'Event Crazy',
                'start': (now + datetime.timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                'end': (now + datetime.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                'location': '3420 Bristol Street, Costa Mesa, CA 92626',
                'lat': 33.694144,
                'lon': -117.885054,
                'timezone': 'America/Los_Angeles',
                }

        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
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

    def test_add_guest_in_bulk(self):
        self.user3 = Account.objects.get(phone='+16572001110')
        # Create event
        url = reverse('event-list')

        now = timezone.now()
        data = {'title': 'Event Crazy',
                'start': (now + datetime.timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                'end': (now + datetime.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                'location': '3420 Bristol Street, Costa Mesa, CA 92626',
                'lat': None,
                'lon': None,
                'timezone': 'US/Pacific'
                }

        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        event_url = response.data['url']
        guests_url = response.data['guests']

        # Invite multiple guests
        # data = [{'guest': reverse('account-detail', kwargs={'pk': self.user2.id})},
        #         {'guest': reverse('account-detail', kwargs={'pk': self.user3.id})}
        #         ]
        data = [{'guest': 'account_id:2'}, {'guest': 'account_id:3'}]
        response = self.client.post(guests_url, json.dumps(data), content_type='application/json')

        # assert returned guests are correct
        expect_name = {'Tidus Hue', 'Patrick Lewis'}

        self.assertEqual(len(response.data), 2, response.data)
        for guest in response.data:
            self.assertIn('url', guest)
            self.assertIn(guest['name'], expect_name)
            self.assertEqual(guest['rsvp'], 0)

    def test_anonymous_user_searches_event(self):
        # Get events
        url = reverse('event-list')
        self.anonymous_client = APIClient()
        response = self.anonymous_client.get(url)

        # assert Only PUBLIC events returned
        for event in response.data['results']:
            self.assertEqual(event['privacy'], Event.PUBLIC)

    def test_anonymous_guest(self):
        "Test that a non-registered guest can RSVP."

        # Create the event
        now = timezone.now()
        data = {'title': "Welcome Anonymous Users",
                'start': (now + datetime.timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                'end': (now + datetime.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                'location': '3420 Bristol Street, Costa Mesa, CA 92626',
                'timezone': 'US/Pacific',
                'status': EventStatus.ACTIVE.value,
                }

        response = self.client.post(reverse('event-list'), data, format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        guests_url = response.data['guests']

        # add guest
        data = {'guest': 'Anonymous McSecret <amcsecret@example.com>'}
        guest_response = self.client.post(guests_url, data, format='json')

        self.assertEqual(guest_response.status_code, status.HTTP_201_CREATED, guest_response.data)

        match = re.search(r"events/(?P<event_id>\d+)/guests/(?P<guest_id>\d+)/$", guest_response.data['url'])
        self.assertIsNotNone(match)
        guest_id = match.group('guest_id')
        event_id = match.group('event_id')

        eg = EventGuest.objects.get(guest_id=guest_id, event_id=event_id)
        self.assertIsNotNone(eg)

        # Anonymous guest hits the RSVP endpoint
        anon_url = reverse('eventguest-detail-anon', kwargs=dict(event_id=event_id, token=eg.token))
        anon_client = APIClient()
        response = anon_client.get(anon_url)
        self.assertEqual(response.data['name'], 'Anonymous McSecret')
        self.assertEqual(response.data['rsvp'], EventGuest.UNDECIDED)

        response = anon_client.put(anon_url, {'rsvp': EventGuest.YES})
        self.assertEqual(response.data['name'], 'Anonymous McSecret')
        self.assertEqual(response.data['rsvp'], EventGuest.YES)

    def test_guest_variations(self):
        "Test different guest formats are accepted."

        # Create the event
        now = timezone.now()
        data = {'title': "Welcome Different user types Users",
                'start': (now + datetime.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                'end': (now + datetime.timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                'location': 'TBD',
                'timezone': 'US/Pacific',
                'status': EventStatus.DRAFT.value,
                }

        response = self.client.post(reverse('event-list'), data, format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        guests_url = response.data['guests']

        guests = (
            'Test Email <guest.one@example.com>',
            'guest.two@example.com',
            'Test Phone <+16575551234>',
            '+16575551235',
            'account_id:{}'.format(self.user2.id),
        )

        self._add_guests(guests_url, guests)  # Asserts they got created

        # test bad formats
        bad_guests = (
            '+199999',
            '+44 20 8366 1177',  # Not E.164 (has spaces)
            '16572001110',
            'phony@email',
            'really no information at all',
            'account_id:10000000',
            'account_id:foo'
        )

        for bad in bad_guests:
            response = self.client.post(guests_url, {'guest': bad}, format='json')
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, (bad, response.data))

        resp = self.client.get(guests_url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['count'], 5)

    @patch('core.tasks.send_sms')
    def test_event_guest_notification(self, send_sms_mock):
        "Guests to events recieve various notifications."

        title = "Send Notifications Test"
        response = self._create_messagetst_event(title=title, status=EventStatus.ACTIVE.value)
        guests_url = response.data['guests']
        event = Event.objects.get(title=title, owner=self.user)

        # Set our mock for sms
        send_sms_mock.return_value = {'error_code': None, 'error_message': None}

        # Guests recieve invitations
        guests = ("someone@example.com", "+16575550001", "account_id:{}".format(self.user2.id))
        self._add_guests(guests_url, guests)

        # should have 2 emails sent
        self.assertEqual(len(mail.outbox), 2)
        email_addresses = {guests[0], self.user2.email}
        for sent_mail in mail.outbox:
            self.assertEqual(sent_mail.subject, "You have been invited to {}".format(title))
            self.assertEqual(len(sent_mail.to), 1)
            address = sent_mail.to[0]
            self.assertIn(address, email_addresses)

            email_addresses.remove(address)

        # should have sent one SMS
        calls = send_sms_mock.mock_calls
        self.assertEqual(len(calls), 1)
        called_phone, sms_msg = send_sms_mock.call_args[0]
        self.assertEqual(called_phone, guests[1])
        invite_txt, url = sms_msg.split("\n")
        self.assertEqual(invite_txt, "Huy Nguyen has invited you to an event.")
        expected_url = reverse('fe:event-rsvp', kwargs={'event_id': event.id})
        self.assertIn(expected_url, url)

    @patch('core.tasks.send_sms')
    def test_draft_to_active_messages(self, send_sms_mock):
        "We send invitiations when we change the event status from Draft to Active."

        title = "Send Notifications Test"
        response = self._create_messagetst_event(title=title, status=EventStatus.DRAFT.value)
        event_url = response.data['url']
        guests_url = response.data['guests']
        event = Event.objects.get(title=title, owner=self.user)

        # Set our mock for sms
        send_sms_mock.return_value = {'error_code': None, 'error_message': None}

        # Add our guests
        guests = ("someone@example.com", "+16575550001", "account_id:{}".format(self.user2.id))
        self._add_guests(guests_url, guests)

        # Should have no emails and no SMS
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(send_sms_mock.call_count, 0)

        # flip status to active
        active_resp = self.client.put(event_url,
                                      {'title': title,
                                       'status': EventStatus.ACTIVE.value,
                                       'start': response.data['start'],
                                       'end': response.data['end'],
                                       'timezone': response.data['timezone']},
                                      format="json")
        self.assertEqual(active_resp.status_code, status.HTTP_200_OK, active_resp.data)

        # Make sure we sent out invites.
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(send_sms_mock.call_count, 1, send_sms_mock.called)

    @patch('core.tasks.send_sms')
    def test_cancelled_event_messages(self, send_sms_mock):
        "We send cancellation notifications when an event is cancelled."

        title = "Will Cancel Soon"
        response = self._create_messagetst_event(title=title, status=EventStatus.ACTIVE.value)
        event_url = response.data['url']
        guests_url = response.data['guests']
        event = Event.objects.get(title=title, owner=self.user)

        guests = ("someone@example.com", "+16575550001", "account_id:{}".format(self.user2.id))
        self._add_guests(guests_url, guests)

        # Reset the test notification channels -- other tests assert creation notifications work.
        mail.outbox = []
        send_sms_mock.reset_mock()

        # cancel the event
        del_resp = self.client.delete(event_url)
        self.assertEqual(del_resp.status_code, status.HTTP_204_NO_CONTENT)

        # check email
        self.assertEqual(len(mail.outbox), 2)
        email_addresses = {guests[0], self.user2.email}
        for sent_mail in mail.outbox:
            self.assertIn("has been cancelled", sent_mail.subject)
            self.assertEqual(len(sent_mail.to), 1)
            address = sent_mail.to[0]
            self.assertIn(address, email_addresses)
            email_addresses.remove(address)

        # check sms
        self.assertEqual(send_sms_mock.call_count, 1)
        called_phone, sms_msg = send_sms_mock.call_args[0]
        self.assertEqual(called_phone, guests[1])
        invite_txt, url = sms_msg.split("\n")
        self.assertIn("cancelled", invite_txt)
        expected_url = reverse('fe:event-cancelled', kwargs={'event_id': event.id})
        self.assertIn(expected_url, url)

    @patch('core.tasks.send_sms')
    def test_changed_event_messages(self, send_sms_mock):
        "We send notifications out when an event changes"

        title = "Will Change Soon"
        response = self._create_messagetst_event(title=title, status=EventStatus.ACTIVE.value)
        event_url = response.data['url']
        guests_url = response.data['guests']
        event = Event.objects.get(title=title, owner=self.user)

        guests = ("someone@example.com", "+16575550001", "account_id:{}".format(self.user2.id))
        self._add_guests(guests_url, guests)

        # Reset the test notification channels -- other tests assert creation notifications work.
        mail.outbox = []
        send_sms_mock.reset_mock()

        # change the event start and end time
        now = timezone.now()
        change_resp = self.client.put(event_url, {
                                      'title': title,
                                      'start': (now + datetime.timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                      'end': (now + datetime.timedelta(hours=25)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                      'location': 'TBD',
                                      'timezone': 'US/Pacific',
                                      'status': EventStatus.ACTIVE.value}, format='json')
        self.assertEqual(change_resp.status_code, status.HTTP_200_OK, change_resp.data)

        # Check email
        self.assertEqual(len(mail.outbox), 2)
        email_addresses = {guests[0], self.user2.email}
        for sent_mail in mail.outbox:
            self.assertEqual('Notice: The event "{}" has changed'.format(title), sent_mail.subject)
            self.assertEqual(len(sent_mail.to), 1)
            address = sent_mail.to[0]
            self.assertIn(address, email_addresses)
            email_addresses.remove(address)

        # Check SMS
        self.assertEqual(send_sms_mock.call_count, 1)
        called_phone, sms_msg = send_sms_mock.call_args[0]
        self.assertEqual(called_phone, guests[1])
        txt, url = sms_msg.split("\n")
        self.assertEquals("{} has changed an event you are invited to.".format(self.user.name), txt)
        expected_url = reverse('fe:event-rsvp', kwargs={'event_id': event.id})
        self.assertIn(expected_url, url)

    def _create_messagetst_event(self, title, status):
        # Create the event
        now = timezone.now()
        data = {'title': title,
                'start': (now + datetime.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                'end': (now + datetime.timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                'location': 'TBD',
                'timezone': 'US/Pacific',
                'status': status}

        return self.client.post(reverse('event-list'), data, format='json')

    def _add_guests(self, guests_url, guests):
        for g in guests:
            resp = self.client.post(guests_url, {'guest': g}, format="json")
            self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

        resp = self.client.get(guests_url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['count'], len(guests))

# EOF
