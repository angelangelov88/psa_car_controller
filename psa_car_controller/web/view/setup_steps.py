"""The setup steps shown in the stepper (1 Sign in -> 2 Get a code -> Done), kept free of Dash so they can be
unit tested. SIGN_IN and GET_CODE are also the ids of the config tabs for those steps."""
SIGN_IN = "login"
GET_CODE = "otp"
DONE = "done"
STEPS = ((SIGN_IN, "Sign in"), (GET_CODE, "Get a code"), (DONE, "Done"))


def step_states(current):
    """(label, state) for each step, where state is "done", "current" or "todo"."""
    order = [step for step, _ in STEPS]
    current_index = order.index(current)
    states = []
    for index, (step, label) in enumerate(STEPS):
        if index < current_index or current == DONE:
            states.append((label, "done"))
        elif index == current_index:
            states.append((label, "current"))
        else:
            states.append((label, "todo"))
    return states
