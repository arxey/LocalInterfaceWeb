import os

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


class Command(BaseCommand):
    help = 'Create the initial administrator; preserve existing credentials unless --reset-password is given.'

    def add_arguments(self, parser):
        parser.add_argument('--reset-password', action='store_true')
        parser.add_argument('--load-demo', action='store_true')

    def handle(self, *args, **options):
        username = os.getenv('ADMIN_USERNAME', '').strip()
        password = os.getenv('ADMIN_PASSWORD', '')
        if not username or not password:
            raise CommandError('ADMIN_USERNAME and ADMIN_PASSWORD must be set.')
        User = get_user_model()
        if len(username) > User._meta.get_field('username').max_length:
            raise CommandError('ADMIN_USERNAME is too long.')
        with transaction.atomic():
            user = User.objects.select_for_update().filter(username=username).first()
            created = user is None
            if user is not None and not (user.is_active and user.is_staff and user.is_superuser):
                raise CommandError('The existing account is not an active administrator. Choose a different ADMIN_USERNAME.')
            if created or options['reset_password']:
                user = user or User(username=username, is_staff=True, is_superuser=True, is_active=True)
                try:
                    user.full_clean(exclude=['password'])
                    validate_password(password, user)
                except ValidationError as exc:
                    raise CommandError('; '.join(exc.messages)) from exc
                user.set_password(password)
                user.save()
        if created and options['load_demo']:
            call_command('load_demo', stdout=self.stdout)
        self.stdout.write(self.style.SUCCESS('Administrator created.' if created else 'Existing administrator preserved.' if not options['reset_password'] else 'Administrator password updated.'))
