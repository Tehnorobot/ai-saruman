package dev.magehome.client;

import adris.altoclef.tasksystem.Task;

/**
 * The root task of the mod inside the UnionClef task chain.
 *
 * <p>All actions of the automation belong to this single root task; the navigation is its child
 * task. The behaviour state of UnionClef is pushed when the task starts and popped when it
 * stops, so it is balanced on every exit.
 */
public final class MageHomeTask extends Task {

    private final MageHomeRuntime runtime;

    public MageHomeTask(MageHomeRuntime runtime) {
        this.runtime = runtime;
    }

    @Override
    protected void onStart() {
        runtime.onTaskStarted();
    }

    @Override
    protected Task onTick() {
        return runtime.onTaskTick();
    }

    @Override
    protected void onStop(Task task) {
        runtime.onTaskStopped();
    }

    @Override
    protected boolean isEqual(Task task) {
        return task instanceof MageHomeTask;
    }

    /**
     * The own root task is finished when the automation is switched off.
     *
     * <p>A task of a disabled automation has nothing to do; it ends then and the behaviour
     * state of UnionClef is released by the ordinary stop (ТЗ.md section 7). The task of a
     * running automation is never finished: it holds the chain of UnionClef and the navigation
     * is its child task.
     */
    @Override
    public boolean isFinished() {
        return !runtime.automationEnabled();
    }

    @Override
    protected String toDebugString() {
        return "mage_home";
    }
}
