"""Custom user model mapped to the legacy ``users`` table."""

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.db import models
from django.utils import timezone

ADMIN_ROLES = {"admin", "super_admin", "system_admin"}


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError("An email address is required")
        email = self.normalize_email(email)
        extra_fields.setdefault("username", email.split("@")[0])
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("role", "employee")
        extra_fields.setdefault("status", "active")
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("role", "super_admin")
        extra_fields.setdefault("status", "active")
        return self._create_user(email, password, **extra_fields)


class User(AbstractBaseUser):
    ROLE_CHOICES = [
        ("admin", "Admin"),
        ("super_admin", "Super Admin"),
        ("hr_manager", "HR Manager"),
        ("manager", "Manager"),
        ("sales", "Sales"),
        ("accountant", "Accountant"),
        ("viewer", "Viewer"),
        ("employee", "Employee"),
    ]
    STATUS_CHOICES = [("active", "Active"), ("inactive", "Inactive")]

    username = models.CharField(max_length=100, unique=True)
    email = models.EmailField(max_length=255, unique=True)
    password = models.CharField(max_length=255)
    first_name = models.CharField(max_length=100, null=True, blank=True)
    last_name = models.CharField(max_length=100, null=True, blank=True)
    role = models.CharField(
        max_length=20, choices=ROLE_CHOICES, default="employee", null=True, blank=True
    )
    employee_id = models.IntegerField(null=True, blank=True)
    role_id = models.IntegerField(null=True, blank=True, db_index=True)
    is_platform_admin = models.BooleanField(default=False, db_index=True)
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default="active", db_index=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["username"]

    class Meta:
        db_table = "users"
        verbose_name = "user"
        verbose_name_plural = "users"

    def __str__(self):
        return self.email

    def get_full_name(self):
        full_name = f"{self.first_name or ''} {self.last_name or ''}".strip()
        return full_name or self.username

    def get_short_name(self):
        return self.first_name or self.username

    @property
    def is_active(self):
        return self.status == "active"

    @property
    def is_staff(self):
        return self.role in ADMIN_ROLES

    @property
    def is_superuser(self):
        return self.role in ADMIN_ROLES

    def has_perm(self, perm, obj=None):
        from apps.core.permissions import user_has_perm

        if "." in perm:
            module, action = perm.split(".", 1)
        else:
            module, action = "system", perm
        return user_has_perm(self, module, action)

    def has_module_perms(self, app_label):
        from apps.core.permissions import user_module_role

        return self.is_superuser or user_module_role(self, app_label) is not None

    def touch_login(self):
        self.last_login = timezone.now()
        self.save(update_fields=["last_login"])
