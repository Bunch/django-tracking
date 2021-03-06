from datetime import datetime
import time
import logging
import re
import traceback

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.core.urlresolvers import reverse, NoReverseMatch
from django.db.utils import DatabaseError
from django.http import Http404
from django.utils.http import cookie_date

from tracking import utils
from tracking.models import Visitor, UntrackedUserAgent, BannedIP

title_re = re.compile('<title>(.*?)</title>')
log = logging.getLogger('tracking.middleware')

class VisitorTrackingMiddleware(object):
    """
    Keeps track of your active users.  Anytime a visitor accesses a valid URL,
    their unique record will be updated with the page they're on and the last
    time they requested a page.

    Records are considered to be unique when the session key and IP address
    are unique together.  Sometimes the same user used to have two different
    records, so I added a check to see if the session key had changed for the
    same IP and user agent in the last 5 minutes
    """

    @property
    def prefixes(self):
        """Returns a list of URL prefixes that we should not track"""

        if not hasattr(self, '_prefixes'):
            self._prefixes = getattr(settings, 'NO_TRACKING_PREFIXES', [])

            if not getattr(settings, '_FREEZE_TRACKING_PREFIXES', False):
                for name in ('MEDIA_URL',):
                    url = getattr(settings, name)
                    if url and url != '/':
                        self._prefixes.append(url)

                try:
                    # finally, don't track requests to the tracker update pages
                    self._prefixes.append(reverse('tracking-refresh-active-users'))
                except NoReverseMatch:
                    # django-tracking hasn't been included in the URLconf if we
                    # get here, which is not a bad thing
                    pass

                settings.NO_TRACKING_PREFIXES = self._prefixes
                settings._FREEZE_TRACKING_PREFIXES = True

        return self._prefixes

    def process_request(self, request):
        # don't process AJAX requests
        if request.is_ajax(): return

        # create some useful variables
        ip_address = utils.get_ip(request)
        user_agent = request.META.get('HTTP_USER_AGENT', '')[:255]

        # retrieve untracked user agents from cache
        ua_key = '_tracking_untracked_uas'
        untracked = cache.get(ua_key)
        if untracked is None:
            log.info('Updating untracked user agent cache')
            untracked = list(UntrackedUserAgent.objects.all())
            cache.set(ua_key, untracked, 3600)

        # see if the user agent is not supposed to be tracked
        for ua in untracked:
            # if the keyword is found in the user agent, stop tracking
            if unicode(user_agent, errors='ignore').find(ua.keyword) != -1:
                log.debug('Not tracking UA "%s" because of keyword: %s' % (user_agent, ua.keyword))
                return

        if hasattr(request, 'session'):
            if not request.session.session_key:
                request.session.save()
            # use the current session key if we can
            session_key = request.session.session_key
        else:
            # otherwise just fake a session key
            session_key = '%s:%s' % (ip_address, user_agent)

        # ensure that the request.path does not begin with any of the prefixes
        for prefix in self.prefixes:
            if request.path.startswith(prefix):
                log.debug('Not tracking request to: %s' % request.path)
                return

        # if we get here, the URL needs to be tracked
        # determine what time it is
        now = datetime.now()

        # Attributes we use when creating a new user
        new_attrs = {
            'session_key': session_key,
            'ip_address': ip_address
        }

        # If we have a visitor_id cookie, use it
        visitor_id = request.COOKIES.get('visitor_id')

        if visitor_id:
            attrs = {'id': visitor_id}
        else:
            attrs = new_attrs

        # for some reason, Visitor.objects.get_or_create was not working here
        try:
            visitor = Visitor.objects.get(**attrs)
        except Visitor.DoesNotExist:
            # add tracking ID to model if specified in the URL
            tid = request.GET.get('tid') or request.GET.get('fb_source')
            if tid:
                get = request.GET.copy()
                attrs['tid'] = tid
                request.GET = get

            visitor = Visitor(**new_attrs)
            log.debug('Created a new visitor: %s' % new_attrs)
        except:
            return

        # determine whether or not the user is logged in
        user = request.user
        if isinstance(user, AnonymousUser):
            user = None

        # update the tracking information
        visitor.user = user
        visitor.user_agent = user_agent

        # if the visitor record is new, update their referrer URL
        if not visitor.last_update:
            visitor.referrer = utils.u_clean(request.META.get('HTTP_REFERER', 'unknown')[:255])

            # reset the number of pages they've been to
            visitor.page_views = 0
            visitor.session_start = now

        visitor.url = request.path
        visitor.page_views += 1
        visitor.last_update = now
        try:
            visitor.save()
        except DatabaseError:
            log.error('There was a problem saving visitor information:\n%s\n\n%s' % (traceback.format_exc(), locals()))

        request.visitor = visitor
        request.session['visitor_id'] = visitor.pk

    def process_response(self, request, response):
        if 'visitor_id' in request.session:
            visitor_id = request.session['visitor_id']
        elif hasattr(request, 'visitor'):
            visitor_id = request.visitor.pk
        else:
            visitor_id = None

        if visitor_id:
            # Set a cookie for the visitor ID using roughly
            # the same parameters as the session cookie
            max_age = request.session.get_expiry_age()

            response.set_cookie(
                'visitor_id',
                visitor_id,
                max_age=max_age,
                expires=cookie_date(time.time() + max_age),
                domain=settings.SESSION_COOKIE_DOMAIN,
                path=settings.SESSION_COOKIE_PATH,
                secure=False,
                httponly=False,
            )

        return response

class VisitorCleanUpMiddleware:
    """Clean up old visitor tracking records in the database"""

    def process_request(self, request):
        timeout = utils.get_cleanup_timeout()

        if str(timeout).isdigit():
            log.debug('Cleaning up visitors older than %s hours' % timeout)
            timeout = datetime.now() - datetime.timedelta(hours=int(timeout))
            Visitor.objects.filter(last_update__lte=timeout).delete()

class BannedIPMiddleware:
    """
    Raises an Http404 error for any page request from a banned IP.  IP addresses
    may be added to the list of banned IPs via the Django admin.

    The banned users do not actually receive the 404 error--instead they get
    an "Internal Server Error", effectively eliminating any access to the site.
    """

    def process_request(self, request):
        key = '_tracking_banned_ips'
        ips = cache.get(key)
        if ips is None:
            # compile a list of all banned IP addresses
            log.info('Updating banned IPs cache')
            ips = [b.ip_address for b in BannedIP.objects.all()]
            cache.set(key, ips, 3600)

        # check to see if the current user's IP address is in that list
        if utils.get_ip(request) in ips:
            raise Http404
