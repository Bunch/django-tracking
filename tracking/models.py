from datetime import datetime, timedelta
import logging
import threading
import traceback

from django.contrib.gis.utils import HAS_GEOIP

if HAS_GEOIP:
    from django.contrib.gis.utils import GeoIP, GeoIPException

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib import admin
from django.db import models
from django.utils.translation import ugettext, ugettext_lazy as _
from tracking import utils

USE_GEOIP = getattr(settings, 'TRACKING_USE_GEOIP', False)
CACHE_TYPE = getattr(settings, 'GEOIP_CACHE_TYPE', 4)

log = logging.getLogger('tracking.models')
geo = threading.local()

class VisitorManager(models.Manager):
    def active(self, timeout=None):
        """
        Retrieves only visitors who have been active within the timeout
        period.
        """
        if not timeout:
            timeout = utils.get_timeout()

        now = datetime.now()
        cutoff = now - timedelta(minutes=timeout)

        return self.get_query_set().filter(last_update__gte=cutoff)

class Visitor(models.Model):
    session_key = models.CharField(max_length=40)
    ip_address = models.CharField(max_length=20, verbose_name='IP address')
    user = models.ForeignKey(User, null=True)
    user_agent = models.CharField(max_length=255)
    referrer = models.CharField(max_length=255)
    url = models.CharField(max_length=255)
    page_views = models.PositiveIntegerField(default=0)
    session_start = models.DateTimeField(db_index=True)
    last_update = models.DateTimeField()
    tid = models.CharField(max_length=50, blank=True, verbose_name='TID')

    objects = VisitorManager()

    def _time_on_site(self):
        """
        Attempts to determine the amount of time a visitor has spent on the
        site based upon their information that's in the database.
        """
        if self.session_start:
            seconds = (self.last_update - self.session_start).seconds

            hours = seconds / 3600
            seconds -= hours * 3600
            minutes = seconds / 60
            seconds -= minutes * 60

            return u'%i:%02i:%02i' % (hours, minutes, seconds)
        else:
            return ugettext(u'unknown')
    time_on_site = property(_time_on_site)

    def _get_geoip_data(self):
        """
        Attempts to retrieve MaxMind GeoIP data based upon the visitor's IP
        """
        global geo

        if not HAS_GEOIP or not USE_GEOIP:
            # go no further when we don't need to
            log.debug('Bailing out.  HAS_GEOIP: %s; TRACKING_USE_GEOIP: %s' % (HAS_GEOIP, USE_GEOIP))
            return None

        if not hasattr(self, '_geoip_data'):
            self._geoip_data = None
            try:
                if not getattr(geo, 'GeoIP', None):
                    geo.GeoIP = GeoIP(cache=CACHE_TYPE)

                self._geoip_data = geo.GeoIP.city(self.ip_address)
            except GeoIPException:
                # don't even bother...
                log.error('Error getting GeoIP data for IP "%s": %s' % (self.ip_address, traceback.format_exc()))
                gip = None # So we try again next time

        return self._geoip_data

    geoip_data = property(_get_geoip_data)

    def _get_geoip_data_json(self):
        """
        Cleans out any dirty unicode characters to make the geoip data safe for
        JSON encoding.
        """
        clean = {}
        if not self.geoip_data: return {}

        for key,value in self.geoip_data.items():
            clean[key] = utils.u_clean(value)
        return clean

    geoip_data_json = property(_get_geoip_data_json)

    class Meta:
        ordering = ('-last_update',)
        unique_together = ('session_key', 'ip_address',)

class UntrackedUserAgent(models.Model):
    keyword = models.CharField(_('keyword'), max_length=100, help_text=_('Part or all of a user-agent string.  For example, "Googlebot" here will be found in "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)" and that visitor will not be tracked.'))

    def __unicode__(self):
        return self.keyword

    class Meta:
        ordering = ('keyword',)
        verbose_name = _('Untracked User-Agent')
        verbose_name_plural = _('Untracked User-Agents')

class BannedIP(models.Model):
    ip_address = models.IPAddressField('IP Address', help_text=_('The IP address that should be banned'))

    def __unicode__(self):
        return self.ip_address

    class Meta:
        ordering = ('ip_address',)
        verbose_name = _('Banned IP')
        verbose_name_plural = _('Banned IPs')

class TrackingEvent(models.Model):
    """
    Used for user event tracking in JavaScript
    """

    name = models.CharField(max_length=50, help_text=_('Name identifying the event'))
    visitor = models.ForeignKey(Visitor, help_text=_('The visitor which generated the event'))
    time = models.DateTimeField(auto_now_add=True, db_index=True)
    data = models.BigIntegerField(null=True, help_text=_('A name-specific identifier providing more information on the event'))

    EVENT_CHOICES = (
        ('click', 'Click'),
        ('hover', 'Hover'),
        ('scroll', 'Scroll'),
        ('focus', 'Focus'),
        ('submit', 'Submit'),
        ('change', 'Change'),
    )
    event = models.CharField(max_length=10, choices=EVENT_CHOICES, help_text=_('The type of UI event'))

class PageVisit(models.Model):
    """
    Record page visits to all URLs
    """

    url = models.CharField(max_length=255)
    query = models.CharField(max_length=255)
    time = models.DateTimeField(auto_now_add=True, db_index=True)
    visitor = models.ForeignKey(Visitor)
