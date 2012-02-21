from django.contrib import admin
from tracking.models import BannedIP, UntrackedUserAgent, Visitor

admin.site.register(BannedIP)
admin.site.register(UntrackedUserAgent)

class VisitorAdmin(admin.ModelAdmin):
    raw_id_fields = ('user',)
    search_fields = ('ip_address', 'url', 'referrer', 'tid')
    list_display = ('user', 'ip_address', 'tid', 'session_start')

admin.site.register(Visitor, VisitorAdmin)
