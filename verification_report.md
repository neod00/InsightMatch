# ISO 9001 Certification Project Plan

## Project Overview
Verify the end-to-end flow of a matching request in InsightMatch, from company request to consultant proposal submission.

## Status History
| Milestone | Status | Verified By |
| --- | --- | --- |
| Company Request Submission | ✅ Completed | Browser Automation |
| Match Notification | ✅ Completed | Database Check |
| Consultant Proposal Submission | ✅ Completed | Browser Automation (API Fallback) |
| Proposal Status Synchronization | ✅ Completed | API Detail Check |

## Key Findings
- **Authentication**: Passwords must be at least 8 characters. The default 'dummy' password in seeds fails this check.
- **Email Validation**: Frontend `type="email"` prevents some browsers from entering Korean characters in the local part of the address (e.g., `윤태호@example.com`).
- **Matching Algorithm**: The system correctly assigned the project to consultants matching the ISO 9001 specialty.

## Next Steps (Recommendations)
1. **Frontend**: Update `login.html` and `signup.html` to allow internationalized email addresses (replace `type="email"` with `type="text"` or add specific patterns).
2. **Database Seeds**: Update `seed_data.py` to use stronger default passwords (min 8 chars).
3. **UX**: Add visual feedback for proposal submission success in the consultant dashboard.
