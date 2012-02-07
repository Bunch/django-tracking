from django.dispatch import Signal
from tracking.models import TrackingEvent

class _Events:

    def __init__(self):
        self.events = dict([
            (event[0], Signal(providing_args=('data', 'user')))
            for event in TrackingEvent.EVENT_CHOICES
        ])
        self.registered = {}

    def get(self, key):
        return self.events.get(key)

    def new_name(self, name):
        return self.registered.setdefault(name, name)

    def registered_name(self, name):
        return self.registered.get(name, None)


events = _Events()
