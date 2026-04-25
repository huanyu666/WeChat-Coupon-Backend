"""
API客户端

"""
import urllib.parse
from utils import http_client as requests
from typing import Dict, Any, Optional


class LinkConversionAPI:
    """
    链接转换API客户端
    """
    
    def __init__(self, zmkey: str, logger=None):
        """
        初始化API客户端
        
        Args:
            zmkey: API密钥
            logger: 日志记录器
        """
        self.zmkey = zmkey
        self.logger = logger
        self.base_url = "https://vxzm.vx6.cn/zmapi/api/user_api.php"
    
    def log(self, message: str, level: str = "info"):
        """记录日志"""
        if self.logger:
            if level == "error":
                self.logger.error(message)
            elif level == "warning":
                self.logger.warning(message)
            else:
                self.logger.info(message)
    
    async def astep1_parse_link(self, link: str) -> Optional[Dict[str, Any]]:
        """
        第一步：解析原始链接，获取appid和page
        
        Args:
            link: 用户发送的原始链接（如 #小程序://美团外卖丨外卖美食奶茶咖啡水果/sjIQrx6NQc0xF9v）
            
        Returns:
            {"appid": "...", "page": "...", "money": "..."}
            失败返回None
        """
        try:
                       
            encoded_link = urllib.parse.quote(link, safe='')
            
                     
            url = f"{self.base_url}?api_type=4&zmkey={self.zmkey}&link={encoded_link}"
            
            self.log(f"步骤1 - 请求URL: {url}")
            
            response = await requests.get(url, timeout=5)
            response.raise_for_status()
            
                  
            result = response.json()
            self.log(f"步骤1 - 响应: {result}")
            
                         
            if not isinstance(result, dict):
                self.log(f"步骤1 失败: 响应格式错误，期望字典但得到 {type(result).__name__}: {result}", "error")
                return None
            
            if result.get("code") == 200:
                return {
                    "appid": result.get("appid"),
                    "page": result.get("page"),
                    "money": result.get("money")
                }
            else:
                self.log(f"步骤1 失败: {result.get('msg', '未知错误')}", "error")
                return None
                
        except Exception as e:
            self.log(f"步骤1 异常: {e}", "error")
            return None
    
    async def astep2_convert_link(self, appid: str, page: str, p_value: str) -> Optional[Dict[str, Any]]:
        """
        第二步：替换P值并转换链接
        
        Args:
            appid: 小程序appid
            page: 小程序页面路径（可能是URL编码的）
            p_value: 用户提供的P值
        
        Returns:
            {"link": "...", "money": "..."}
            失败返回None
        """
        try:
            self.log(f"步骤2 - 原始page: {page}")
            
            import re
                                             
                                      
                   
                                     
                               
                                        
            p_pattern_encoded = r'%26p%3D([^&%]*)'
            p_pattern_decoded = r'([?&])p=([^&%]*)'
            
                                  
            has_encoded = bool(re.search(p_pattern_encoded, page))
            has_decoded = bool(re.search(p_pattern_decoded, page))
            
                              
            encoded_p_value = urllib.parse.quote(p_value, safe='')
            
            if not has_encoded and not has_decoded:
                                           
                                          
                                               
                last_param_start = max(page.rfind('&'), page.rfind('?'))
                
                if last_param_start >= 0:
                                         
                                            
                    last_param = page[last_param_start:]
                    percent26_count = last_param.count('%26')
                    is_url_encoded = percent26_count >= 2
                    self.log(f"步骤2 - 最后一个参数: {last_param[:100]}...")
                    self.log(f"步骤2 - %26出现次数: {percent26_count}, 是否URL编码: {is_url_encoded}")
                    
                    if is_url_encoded:
                        modified_page = f"{page}%26p%3D{encoded_p_value}"
                        self.log(f"步骤2 - 未找到P值参数，将在末尾添加 %26p%3D={encoded_p_value}")
                    else:
                                           
                        if page.endswith('&') or page.endswith('?'):
                            modified_page = f"{page}p={encoded_p_value}"
                        else:
                                                          
                            separator = "&" if "?" in page else "?"
                            modified_page = f"{page}{separator}p={encoded_p_value}"
                        self.log(f"步骤2 - 未找到P值参数，将在末尾添加 &p={encoded_p_value}")
                else:
                                                       
                    separator = "&" if "?" in page else "?"
                    modified_page = f"{page}{separator}p={encoded_p_value}"
                    self.log(f"步骤2 - 未找到P值参数，将在末尾添加 {separator}p={encoded_p_value}")
                
                self.log(f"步骤2 - 添加P值后: {modified_page[:200]}...")
            else:
                             
                encoded_matches = list(re.finditer(p_pattern_encoded, page))
                decoded_matches = list(re.finditer(p_pattern_decoded, page))
                total_count = len(encoded_matches) + len(decoded_matches)
                self.log(f"步骤2 - 找到 {total_count} 个P参数（编码: {len(encoded_matches)}, 未编码: {len(decoded_matches)}）")
                
                for i, match in enumerate(encoded_matches, 1):
                    old_p_value = match.group(1)
                    self.log(f"步骤2 - P参数 {i}（编码），当前值: {old_p_value[:50]}...")
                
                for i, match in enumerate(decoded_matches, 1):
                    old_p_value = match.group(2)
                    self.log(f"步骤2 - P参数 {len(encoded_matches) + i}（未编码），当前值: {old_p_value[:50]}...")
                
                                       
                def replace_encoded_p(match):
                    return f'%26p%3D{encoded_p_value}'
                
                def replace_decoded_p(match):
                    separator = match.group(1)         
                    return f'{separator}p={encoded_p_value}'
                
                                
                modified_page = re.sub(p_pattern_encoded, replace_encoded_p, page)
                modified_page = re.sub(p_pattern_decoded, replace_decoded_p, modified_page)
                self.log(f"步骤2 - 替换所有P值后: {modified_page[:200]}...")
            
                            
            encoded_page = urllib.parse.quote(modified_page, safe='')
            self.log(f"步骤2 - URL编码后的page: {encoded_page[:200]}...")
                     
            url = f"{self.base_url}?api_type=1&zmkey={self.zmkey}&appid={appid}&page={encoded_page}"
            self.log(f"步骤2 - 请求URL: {url}")
            response = await requests.get(url, timeout=5)
            response.raise_for_status()
            
                  
            result = response.json()
            self.log(f"步骤2 - 响应: {result}")
            
            if result.get("code") == 200:
                return {
                    "link": result.get("link"),
                    "money": result.get("money")
                }
            else:
                self.log(f"步骤2 失败: {result.get('msg', '未知错误')}", "error")
                return None
                
        except Exception as e:
            self.log(f"步骤2 异常: {e}", "error")
            return None
    
    async def agenerate_custom_link(self, appid: str, path: str) -> Optional[str]:
        """
        生成自定义链接（不需要P值）
        
        Args:
            appid: 小程序appid
            path: 小程序页面路径
            
        Returns:
            转换后的链接，失败返回None
        """
        try:
            self.log(f"生成自定义链接 - appid: {appid}, path: {path}")
            
                          
            encoded_path = urllib.parse.quote(path, safe='')
            self.log(f"生成自定义链接 - URL编码后的path: {encoded_path[:200]}...")
            
                     
            url = f"{self.base_url}?api_type=1&zmkey={self.zmkey}&appid={appid}&page={encoded_path}"
            self.log(f"生成自定义链接 - 请求URL: {url}")
            
            response = await requests.get(url, timeout=5)
            response.raise_for_status()
            
                  
            result = response.json()
            self.log(f"生成自定义链接 - 响应: {result}")
            
            if result.get("code") == 200:
                link = result.get("link")
                self.log(f"生成自定义链接成功: {link}")
                return link
            else:
                self.log(f"生成自定义链接失败: {result.get('msg', '未知错误')}", "error")
                return None
                
        except Exception as e:
            self.log(f"生成自定义链接异常: {e}", "error")
            return None
    
    def _replace_p_value(self, page: str, p_value: str) -> str:
        """
        替换或添加P值参数
        
        Args:
            page: 原始页面路径
            p_value: 新的P值
            
        Returns:
            修改后的页面路径
        """
                   
        if "&p=" in page or "?p=" in page:
                         
            import re
            modified = re.sub(r'([?&])p=[^&]*', f'\\1p={p_value}', page)
            return modified
        else:
                           
            separator = "&" if "?" in page else "?"
            return f"{page}{separator}p={p_value}"
    
    async def aconvert_link(self, original_link: str, p_value: str) -> Optional[str]:
        """
        完整的链接转换流程
        
        Args:
            original_link: 用户发送的原始链接
            p_value: 用户提供的P值
            
        Returns:
            转换后的链接，失败返回None
        """
                    
        step1_result = await self.astep1_parse_link(original_link)
        if not step1_result:
            return None
        
        appid = step1_result["appid"]
        page = step1_result["page"]
        
                     
        step2_result = await self.astep2_convert_link(appid, page, p_value)
        if not step2_result:
            return None
        
        return step2_result["link"]
