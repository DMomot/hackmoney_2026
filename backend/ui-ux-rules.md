# UX / UI Guardrails

This document defines mandatory UX principles for all frontend and design work.
Cursor must follow these rules when generating UI components, layouts, or flows.

---

# 1. Core Meta Rules (Always Apply)

1. The user should not think.
2. The user should not wait.
3. The user should not make mistakes (and if they do — recovery must be easy).

---

# 2. Cognitive Load Rules

## 2.1 Hick's Law
- Limit choices per screen.
- One primary action per screen.
- Prefer 3 options over 10.

## 2.2 Miller's Law
- Do not overload with more than 7 visual objects per group.
- Group related elements.
- Use progressive disclosure for advanced settings.

## 2.3 Recognition over Recall (Nielsen #6)
- Keep important navigation visible.
- Use autocomplete, breadcrumbs, recent items.

## 2.4 Jakob's Law
- Follow familiar Web3 / fintech UI patterns.
- Do not invent new navigation patterns.
- Use standard wallet connect modals.

---

# 3. Layout & Visual Hierarchy

## 3.1 One Task Per Screen
Each screen must have one primary goal.

## 3.2 Visual Hierarchy
- Important elements larger and higher.
- Use F-pattern scanning.
- If everything looks equal, nothing is important.

## 3.3 Whitespace
- Use spacing to group elements.
- Avoid visual clutter.

## 3.4 Affordance & Signifiers
- Buttons must look clickable.
- Non-clickable elements must not look interactive.
- Use clear action labels ("Compare Prices", not "Continue").

---

# 4. Error Prevention & Safety

## 4.1 Error Prevention (Poka-yoke)
- Disable actions if inputs are invalid.
- Validate inputs in real-time.
- Prevent invalid blockchain transactions.

## 4.2 Error Recovery
Error messages must:
- Explain what happened.
- Explain why.
- Provide next action.

Never show generic errors like "Error 400".

---

# 5. Performance & Feedback

## 5.1 Doherty Threshold
- UI interactions must feel <400ms.
- Use skeleton screens instead of spinners where possible.

## 5.2 Feedback
Every user action must have visual feedback:
- Button state change
- Loading indicator
- Success confirmation
- Error state

---

# 6. State Management (Mandatory)

Every major UI component must support:
- Loading state
- Empty state
- Error state
- Success state

No screen should exist in a single "perfect" state only.

---

# 7. Minimalism

- Remove unnecessary elements.
- Avoid duplicate information.
- If a UI element does not serve a clear purpose, remove it.

---

# 8. Trust & Aesthetic

- Clean layout
- Consistent spacing
- Consistent typography
- Clear financial data formatting
- Transparent fees

Design must feel safe for financial usage.

---

# 9. Accessibility (Required)

- Minimum 44x44px tap targets
- Contrast ratio 4.5:1
- Keyboard navigation support
- Do not rely only on color to convey meaning

---

# 10. Advanced Users

- Provide shortcuts for power users.
- Keep interface simple for beginners.
- Support progressive complexity.

---

# Validation Before Merge

Before shipping UI:
- Is there one primary action?
- Are all states implemented?
- Are errors helpful?
- Is cognitive load minimal?
- Does it match common fintech patterns?