# Changelog

## 1.3.1 — 2026-10-16

### Fixed

- Keep search deletion confirmation next to the selected search and scroll it into view. On long dashboard cards the confirmation could previously appear far above the clicked button, leaving other actions disabled while waiting for an unseen confirmation.

### Added

- Original Medi Assistant calendar/medical icon with transparent background, bundled as 256 px and 512 px local brand images for Home Assistant 2026.3+.
- README icon preview and compatibility note; older supported HA versions retain their existing behavior.


## 1.3.0 — 2026-10-16

### Added

- Administrator-only Delete search button on the appointments card, with account/search confirmation and list refresh after deletion.
- Account-scoped delete_search action clears persisted notification state and removes the native search subentry without cancelling booked appointments.


## 1.2.0 — 2026-10-16

### Added

- Actions get_search_filters and create_search for chatbot and automation access. Searches use the same account subentries, validation and titles as the native configuration flow.
- Region-scoped filter validation, date range checks and duplicate protection for repeated or concurrent creation requests.
- Individual mobile notifications for up to three nearest new appointments per search and poll, plus an overflow summary without booking. Per-offer tags keep booking results attached to the selected appointment.
- Optional account-level Home Assistant view path for notification taps on Android and iOS; clearer search-wide mute/delete labels.

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
