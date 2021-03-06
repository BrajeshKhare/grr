#!/usr/bin/env python
"""API Authorization Manager."""




import yaml

import logging

from grr.gui import api_call_router
from grr.lib import config_lib
from grr.lib import registry
from grr.lib import stats
from grr.lib.authorization import auth_manager


class Error(Exception):
  """Base class for auth manager exception."""


class InvalidAPIAuthorization(Error):
  """Used when an invalid authorization is defined."""


class ApiCallRouterNotFoundError(Error):
  """Used when a router with a given name can't be found."""


class APIAuthorization(object):
  """Authorization for users/groups to use an API handler."""

  def __init__(self):
    super(APIAuthorization, self).__init__()
    self.router = None
    self.users = []
    self.groups = []
    self.router_params = {}

  @property
  def router_params_dict(self):
    result = {}
    for item in self.router_params.items:
      if item.invalid:
        raise InvalidAPIAuthorization(
            "Invalid value in router %s configuration: %s" % (self.router,
                                                              item.key))

      result[item.key] = item.value

  @staticmethod
  def ParseYAMLAuthorizationsList(yaml_data):
    """Parses YAML data into a list of APIAuthorization objects."""
    try:
      raw_list = list(yaml.safe_load_all(yaml_data))
    except (ValueError, yaml.YAMLError) as e:
      raise InvalidAPIAuthorization("Invalid YAML: %s" % e)

    result = []
    for auth_src in raw_list:
      auth = APIAuthorization()
      auth.router = auth_src["router"]
      auth.users = auth_src.get("users", [])
      auth.groups = auth_src.get("groups", [])
      auth.router_params = auth_src.get("router_params", {})

      result.append(auth)

    return result


class APIAuthorizationManager(object):
  """Manages loading API authorizations and enforcing them."""

  def _CreateRouter(self, name, params=None):
    try:
      router_cls = api_call_router.ApiCallRouter.classes[name]
    except KeyError:
      raise ApiCallRouterNotFoundError("%s not a valid router" % name)

    params = params or {}
    return router_cls(**params)

  def __init__(self):
    """Initializes the manager by reading the config file."""

    self.routers = []
    self.auth_manager = auth_manager.AuthorizationManager()

    self.default_router = self._CreateRouter(config_lib.CONFIG[
        "API.DefaultRouter"])

    if config_lib.CONFIG["API.RouterACLConfigFile"]:
      logging.info("Using API router ACL config file: %s",
                   config_lib.CONFIG["API.RouterACLConfigFile"])

      with open(config_lib.CONFIG["API.RouterACLConfigFile"], mode="rb") as fh:
        acl_list = APIAuthorization.ParseYAMLAuthorizationsList(fh.read())

      if not acl_list:
        raise InvalidAPIAuthorization("No entries added from "
                                      "RouterACLConfigFile.")

      for index, acl in enumerate(acl_list):
        router = self._CreateRouter(acl.router, params=acl.router_params)
        self.routers.append(router)

        router_id = str(index)
        self.auth_manager.DenyAll(router_id)

        for group in acl.groups:
          self.auth_manager.AuthorizeGroup(group, router_id)

        for user in acl.users:
          self.auth_manager.AuthorizeUser(user, router_id)

  def GetRouterForUser(self, username):
    """Returns a router corresponding to a given username."""

    for index, router in enumerate(self.routers):
      router_id = str(index)

      if self.auth_manager.CheckPermissions(username, router_id):
        logging.debug("Matched router %s to user %s", router.__class__.__name__,
                      username)
        return router

    logging.debug("No router ACL rule match for user %s. Using default "
                  "router %s", username, self.default_router.__class__.__name__)
    return self.default_router

# Set in APIACLInit
API_AUTH_MGR = None


class APIACLInit(registry.InitHook):
  """Init hook that initializes API auth manager."""

  @staticmethod
  def InitApiAuthManager():
    global API_AUTH_MGR
    API_AUTH_MGR = APIAuthorizationManager()

    stats.STATS.RegisterCounterMetric("grr_api_auth_success",
                                      fields=[("handler", str), ("user", str)])
    stats.STATS.RegisterCounterMetric("grr_api_auth_fail",
                                      fields=[("handler", str), ("user", str)])

  def RunOnce(self):
    allowed_contexts = ["AdminUI Context"]

    for ctx in allowed_contexts:
      if ctx in config_lib.CONFIG.context:
        self.InitApiAuthManager()
        return

    logging.debug("Not initializing API Authorization Manager, as it's not "
                  "supposed to be used by this component.")
