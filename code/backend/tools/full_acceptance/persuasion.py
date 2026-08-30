from __future__ import annotations


def credible_group_replies(active: dict) -> tuple[str, ...]:
    """Return credible, agenda-specific replies for automated real-route play.

    The order is intentional: an agenda may mention generic words such as
    ``材料`` or ``证据`` while its actual subject is environmental treatment or
    public oversight.  More specific subjects must win before the material
    protection fallback.
    """

    agenda = str(active.get("agenda", ""))
    if any(marker in agenda for marker in ("巡察", "整改", "逾期", "自查", "终局")):
        return (
            "终局汇报按已完成、逾期、证据不足三类逐项列示，不把承诺写成结果；每项附责任人、原始记录和下一节点。",
            "签约、环保、医疗和资金分别附原始依据，缺什么就如实写缺什么，巡察组可抽查底稿并保留更正前版本。",
        )
    if any(marker in agenda for marker in ("公开", "监督", "舆情", "记者")):
        return (
            "三日内公开台账版本、检测来源和更正记录，原始材料与对外口径并列保留，记者可依法查阅公开材料。",
            "公开页面保留历史版本，未回答的问题进入公开待办并标明责任部门、纠正时间和依据。",
        )
    if any(marker in agenda for marker in ("环保", "治疗", "复检", "取样")):
        return (
            "明早由第三方检测机构和县医院分别进场，水样双份封存、编号盲检，儿童按原始名单逐人复检并建立转诊清单。",
            "县医院负责儿童复检和转诊，第三方机构负责双份盲检；家属和村民代表见证封样，原件、副本和每次交接都登记去向。",
        )
    if any(marker in agenda for marker in ("宗族", "迁坟", "安置")):
        return (
            "周氏和散姓各推一名代表共同见证，镇干部只记录，县搬迁专班按公开政策复核；争议户单列继续协商，绝不替住户签字。",
            "迁坟礼序逐户确认，安置、医疗和就学逐户核权；确认表由住户、镇和县专班各留一份，更正保留原版本和经办人。",
            "代表只能见证、不能替别人决定；签字只确认材料已记录，不代表放弃异议，政策没有依据的事项不写成承诺。",
        )
    if any(marker in agenda for marker in ("材料", "保护", "证据", "交代")):
        return (
            "原始材料今晚由两名经手人共同编号封存，制作只读副本并记录交接时间；明早交县纪委指定人员签收。",
            "封存、复制、移交分别留痕，赵建国可在纪检人员在场时逐项说明，任何人不得私自删改。",
        )
    return (
        "我承认当前记录里的差距，不把未完成写成完成；明早由县镇共同核对原始台账，三日内公开差额、责任人和可复核记录。",
        "未完成事项继续标注逾期，原始表和更正表并列保留，任何人不得为达标补签或改口。",
    )
