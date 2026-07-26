#!/usr/bin/env python3
import argparse
import json
import random
from types import SimpleNamespace

from discover_minio_tasks import DEFAULT_BUCKETS, build_discovery


def is_rebuildable(task):
    return task.get("source_ready") or (task["status"] == "done" and task["has_mineru"] and task["has_minerupopo"])


def task_label(task):
    pdf_name = task.get("pdf_name") or "(unknown PDF name)"
    source_hash = task.get("source_hash") or "-"
    popo_job_id = task.get("popo_job_id") or task["job_id"]
    mineru_job_id = task.get("mineru_job_id") or "-"
    if mineru_job_id != "-" and mineru_job_id != popo_job_id:
        job_trace = f"popo_job={popo_job_id} / mineru_job={mineru_job_id}"
    else:
        job_trace = f"job={popo_job_id}"
    return (
        f"{pdf_name}\n"
        f"  trace: {task['pdf_id']} / {job_trace} / source_hash={source_hash} / "
        f"mineru_link={task.get('mineru_link_strategy', 'unknown')} / "
        f"source={task['status']} / rebuild={task.get('rebuild_state', 'unknown')}"
    )


def build_prompt(args):
    discovery_args = SimpleNamespace(
        container=args.container,
        input_bucket=args.input_bucket,
        mineru_bucket=args.mineru_bucket,
        minerupopo_bucket=args.minerupopo_bucket,
        raw_bucket=args.raw_bucket,
        pdf_id=None,
        job_id=None,
        limit=None,
    )
    discovery = build_discovery(discovery_args)
    tasks = discovery["tasks"]
    rebuildable = [task for task in tasks if is_rebuildable(task)]
    not_started = [task for task in rebuildable if task.get("rebuild_state") == "not_started"]
    failed = [task for task in rebuildable if task.get("rebuild_state") == "failed"]
    needs_review = [task for task in rebuildable if task.get("rebuild_state") == "needs_review"]
    stale_running = [task for task in rebuildable if task.get("rebuild_state") == "stale_running"]
    retryable = failed + needs_review + stale_running
    continuable = not_started + retryable
    published = [task for task in rebuildable if task.get("rebuild_state") == "published"]
    blocked = [task for task in tasks if not is_rebuildable(task)]

    sample_continue = continuable[: args.sample]
    sample_retry = retryable[: args.sample]
    sample_published = published[: args.sample]
    random_task = None
    if not_started:
        rng = random.Random(args.seed)
        random_task = rng.choice(not_started)

    data = {
        "mode": "pdf-clean-markdown-rebuild-startup",
        "buckets": discovery["buckets"],
        "counts": {
            "total": len(tasks),
            "source_ready": len(rebuildable),
            "not_started": len(not_started),
            "retryable": len(retryable),
            "failed": len(failed),
            "needs_review": len(needs_review),
            "stale_running": len(stale_running),
            "continuable": len(continuable),
            "published": len(published),
            "blocked": len(blocked),
        },
        "sample_continue": sample_continue,
        "sample_retry": sample_retry,
        "sample_published": sample_published,
        "random_test_candidate": random_task,
    }
    return data


def print_markdown(data):
    counts = data["counts"]
    print("# PDF clean markdown rebuild 启动菜单\n")
    print("当前 MinIO 任务池：")
    print(f"- 总任务：{counts['total']}")
    print(f"- 源资产已就绪：{counts['source_ready']}")
    print(f"- 未开始重建：{counts['not_started']}")
    print(f"- 可继续处理：{counts['continuable']}")
    print(f"- 可重试/复核：{counts['retryable']}（failed={counts['failed']}, needs_review={counts['needs_review']}, stale_running={counts['stale_running']}）")
    print(f"- 已发布到 eduassets-raw：{counts['published']}")
    print(f"- 阻塞或不完整任务：{counts['blocked']}\n")

    print("请选择任务模式，回复编号或文字即可：")
    print("1. 全部重建：重建所有 source_ready 任务，包括 published，适合规则升级后的全量回归；发布前必须确认覆盖/版本策略。")
    print("2. 继续重建：处理 not_started、failed、needs_review、stale_running，跳过 published。")
    print("3. 重试失败：只处理 failed、needs_review、经确认的 stale_running。")
    print("4. 选择重建：从下面样例或你给出的 pdf_id/job_id 中选择一个或多个任务。")
    print("5. 随机测试重建：默认从 not_started 中随机挑一个做端到端测试。")
    print("6. 只发现不执行：只输出任务清单和状态，不做重建。")
    print("7. 本地文件夹重建：对你给出的本地 MinerU/PDF extraction folder 执行同一套流程。\n")

    if data["random_test_candidate"]:
        print("随机测试候选：")
        print(f"- {task_label(data['random_test_candidate'])}\n")

    if data["sample_continue"]:
        print("可继续样例：")
        for task in data["sample_continue"]:
            print(f"- {task_label(task)}")
        print()

    if data["sample_retry"]:
        print("可重试/复核样例：")
        for task in data["sample_retry"]:
            print(f"- {task_label(task)}")
        print()

    if data["sample_published"]:
        print("已入 raw 样例：")
        for task in data["sample_published"]:
            print(f"- {task_label(task)}")
        print()

    print("执行规则：收到你的选择后，先复述将执行的范围、是否会写入 eduassets-raw，再等你确认；确认后才开始重建或发布。")


def main():
    parser = argparse.ArgumentParser(description="Print a startup task menu for pdf-clean-markdown-rebuild.")
    parser.add_argument("--container", default="minio")
    parser.add_argument("--input-bucket", default=DEFAULT_BUCKETS["input"])
    parser.add_argument("--mineru-bucket", default=DEFAULT_BUCKETS["mineru"])
    parser.add_argument("--minerupopo-bucket", default=DEFAULT_BUCKETS["minerupopo"])
    parser.add_argument("--raw-bucket", default=DEFAULT_BUCKETS["raw"])
    parser.add_argument("--sample", type=int, default=5)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    data = build_prompt(args)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print_markdown(data)


if __name__ == "__main__":
    main()
