"""Vehicle location: GPS points reported by the FMS Field app, tracking
sessions, and the Rescue Map.

Permission codes (five-action rule, registered here so they exist as
soon as the models are imported at startup):

  vehiclelocation.create  report locations: the assigned vehicle (manual
                          capture and automatic tracking), and -- with
                          .view as well -- a manual capture of any
                          vehicle the user's org scope covers
  vehiclelocation.view    see the Rescue Map, detail and history for the
                          vehicles the user's org scope covers

Which ROLES get them is configured in the Roles screen; nothing here
names a role.
"""
from app.core.security.registry import registry

registry.register("vehiclelocation.view", "vehiclelocation", "view",
                  "View vehicle locations on the Rescue Map")
registry.register("vehiclelocation.create", "vehiclelocation", "create",
                  "Report vehicle locations (capture and tracking)")
