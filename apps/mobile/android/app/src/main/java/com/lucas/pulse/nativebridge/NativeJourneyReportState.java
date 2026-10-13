package com.lucas.pulse.nativebridge;

/** Process-local single-flight and generation gate for one explicit report export. */
final class NativeJourneyReportState {
    private long nextId;
    private Operation active;
    private boolean destroyed;

    synchronized Operation begin(int days, long sessionGeneration) {
        if (destroyed || active != null || (days != 30 && days != 90 && days != 365)
                || sessionGeneration < 0) return null;
        nextId++;
        if (nextId == 0) nextId++;
        active = new Operation(nextId, days, sessionGeneration);
        return active;
    }

    synchronized boolean isCurrent(Operation operation) {
        return operation != null && active == operation;
    }

    synchronized boolean isCurrent(Operation operation, long sessionGeneration) {
        return isCurrent(operation) && operation.sessionGeneration == sessionGeneration;
    }

    synchronized boolean finish(Operation operation) {
        if (!isCurrent(operation)) return false;
        active = null;
        return true;
    }

    synchronized Operation retire() {
        Operation previous = active;
        active = null;
        return previous;
    }

    synchronized Operation destroy() {
        destroyed = true;
        return retire();
    }

    static final class Operation {
        final long id;
        final int days;
        final long sessionGeneration;

        private Operation(long id, int days, long sessionGeneration) {
            this.id = id;
            this.days = days;
            this.sessionGeneration = sessionGeneration;
        }
    }
}
