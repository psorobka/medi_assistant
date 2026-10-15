# Changelog

## 1.1.0 — 2026-10-15

### Added

- Book an exact appointment from a mobile_app notification without opening the application. The Book button refers to the first appointment in the notification.
- Home Assistant actions to list booking offers, book appointments, list planned appointments and cancel a specific appointment.
- Bundled My appointments dashboard card: planned visits for all accounts, cancellation with confirmation, saved searches and their available slots with a Book button.
- Explicit confirmation of late cancellations, automatic refresh after operations and a shortcut to the native integration configuration for adding searches.
- Local Home Assistant launcher with local trusted access and persistent debug logging that omits patient names and authentication URL parameters.
- Python and DOM regression tests, a CI job for card interactions and documented PR/release procedures.

### Safety and compatibility

- Booking revalidates the exact slot and blocks payment or additional-consent flows. Account-scoped random identifiers expire after 30 minutes or integration reload/restart.
- Cancellation revalidates the planned appointment and cancellation permission. Ambiguous writes are not automatically retried.
- Existing account authentication, editable searches, sensors and notifications remain supported. Existing actions selecting one search retain their response format.
- The card shows up to 20 nearest offers per search and explicitly reports truncation. Adding a search opens the native integration settings; the form remains managed by Home Assistant.
- Polish and English descriptions are provided. Reload the HA frontend after upgrading to load the new card module.

## 1.0.0

- Initial Medi Assistant integration with Medicover accounts, search subentries, appointment sensors, filter refresh, session keepalive and optional notifications.
