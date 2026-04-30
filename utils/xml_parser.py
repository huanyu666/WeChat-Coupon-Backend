"""
XML消息解析工具
"""
from typing import Dict, Any, Iterable

try:
    from defusedxml import ElementTree as ET
except ImportError:  # pragma: no cover - dependency is declared in requirements.txt
    import xml.etree.ElementTree as ET


def parse_xml_message(xml_str: str) -> Dict[str, Any]:
    """
    解析微信XML消息
    
    Args:
        xml_str: XML格式的消息字符串
        
    Returns:
        消息字典，包含所有XML标签和对应的值
    """
    root = ET.fromstring(xml_str)
    msg = {}
    
    for child in root:
        msg[child.tag] = child.text
    
    return msg


def extract_xml_fields(xml_str: str, field_names: Iterable[str]) -> Dict[str, Any]:
    """
    轻量提取 XML 指定字段，避免在只需要少量字段时构造整条消息字典。

    Args:
        xml_str: XML 格式的消息字符串
        field_names: 需要提取的标签名

    Returns:
        仅包含命中字段的字典
    """
    wanted = {str(name) for name in field_names if str(name)}
    if not wanted:
        return {}

    root = ET.fromstring(xml_str)
    result: Dict[str, Any] = {}
    for child in root:
        tag = child.tag
        if tag in wanted:
            result[tag] = child.text
            if len(result) >= len(wanted):
                break
    return result
