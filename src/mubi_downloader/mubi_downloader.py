#!/usr/bin/env python3
import os
import re
import json
import logging
import requests
import base64
import shutil
from dataclasses import dataclass
from typing import Optional, Dict, List
from bs4 import BeautifulSoup
from .auth_manager import AuthManager
from .drm import build_pssh, fetch_decryption_key

@dataclass
class MovieInfo:
    """Data class for storing movie information"""
    film_id: str
    title: str
    year: str
    available_countries: List[str]
    film_slug: str
    
    @property
    def full_title(self) -> str:
        return f"{self.title} ({self.year})"
    
    @property
    def mubi_url(self) -> str:
        return f"https://mubi.com/films/{self.film_slug}"

class MovieSearch:
    """Handles movie search functionality and result parsing"""
    
    def __init__(self, base_url: str = "https://whatsonmubi.com/?q="):
        self.base_url = base_url
        self.logger = logging.getLogger('MovieSearch')
    
    def get_user_location(self) -> str:
        """
        Determines user's country code based on IP address.
        
        Returns:
            str: Two-letter country code in lowercase
        """
        try:
            response = requests.get('http://ip-api.com/json/')
            data = response.json()
            return data['countryCode'].lower()
        except Exception as e:
            self.logger.error(f"Failed to get location: {e}")
            return input("Enter your country code (e.g., us, uk): ").lower()

    def search_movie(self, query: str) -> Optional[MovieInfo]:
        """
        Searches for a movie on Mubi.
        
        Args:
            query (str): Movie search query
            
        Returns:
            Optional[MovieInfo]: Movie information if found, None otherwise
        """
        try:
            response = requests.get(self.base_url + query)
            soup = BeautifulSoup(response.text, 'html.parser')
            from bs4 import Tag
            result = soup.find('div', class_='film')
            
            if not result or not isinstance(result, Tag):
                self.logger.info("No valid results found, entering manual mode")
                return self._handle_manual_entry()
                
            # Safely extract data with type checking
            title_elem = result.find('h2')
            if not title_elem or not isinstance(title_elem, Tag):
                self.logger.error("Could not find valid title element")
                return self._handle_manual_entry()
                
            # Get attributes directly from the Tag
            film_id = result.attrs.get('data-id')
            if not film_id:
                self.logger.error("Could not find film ID")
                return self._handle_manual_entry()
                
            year = result.attrs.get('data-year')
            if not year:
                self.logger.error("Could not find year")
                return self._handle_manual_entry()
                
            countries_elem = result.find('p', attrs={'class': 'film-showing'})
            available_countries = []
            if countries_elem and isinstance(countries_elem, Tag) and countries_elem.string:
                available_countries = [c.strip() for c in countries_elem.string.split(",")]
                
            link_elem = result.find('a')
            film_slug = ""
            if link_elem and isinstance(link_elem, Tag):
                href = link_elem.attrs.get('href', '')
                if href:
                    film_slug = href.split('/')[-1]
            
            return MovieInfo(
                film_id=str(film_id),
                title=title_elem.string.strip() if title_elem.string else title_elem.text.strip(),
                year=str(year),
                available_countries=available_countries,
                film_slug=film_slug
            )
            
        except Exception as e:
            self.logger.error(f"Search failed: {e}")
            return self._handle_manual_entry()
    
    def _handle_manual_entry(self) -> MovieInfo:
        """Handles manual entry of movie information"""
        return MovieInfo(
            film_id=input("Enter the film ID: "),
            title=input("Enter the film title: "),
            year=input("Enter the film year: "),
            available_countries=[],
            film_slug=""
        )

class DownloadManager:
    """Handles video download and decryption operations"""
    
    def __init__(self, auth_manager: AuthManager, download_folder: str = "download"):
        self.auth_manager = auth_manager
        self.download_folder = download_folder
        self.logger = logging.getLogger('DownloadManager')
        self._ensure_download_folder()
        
        # Get user's country code
        try:
            response = requests.get('http://ip-api.com/json/')
            self.country_code = response.json()['countryCode'].upper()
            self.logger.debug(f"Detected country code: {self.country_code}")
        except Exception as e:
            self.logger.error(f"Failed to get country code: {e}")
            self.country_code = 'US'  # Default fallback
            self.logger.debug(f"Using fallback country code: {self.country_code}")
    
    def _ensure_download_folder(self):
        """Creates download folder if it doesn't exist"""
        os.makedirs(self.download_folder, exist_ok=True)
        os.makedirs(os.path.join(self.download_folder, "temp"), exist_ok=True)
    
    def _get_encryption_info(self, api_url: str) -> tuple[str, str]:
        """
        Retrieves encryption information for the video.
        
        Args:
            api_url (str): API URL for retrieving the secure URL

        Returns:
            tuple[str, str]: Tuple of (decryption_key, secure_url)
        """
        try:
            headers = self._prepare_headers()
            if not headers.get('dt-custom-data'):
                raise ValueError("Missing required dt-custom-data header")
            
            # Extract film ID and check region access
            film_id = api_url.split("/")[-3]
            check_url = f'https://api.mubi.com/v3/films/{film_id}'
            self.logger.debug(f"Checking film region access: {check_url}")
            check_response = requests.get(check_url, headers=headers)
            check_data = check_response.json()
            
            if 'available' in check_data and not check_data['available']:
                self.logger.error("Film not available in your region")
                raise ValueError("This film is not available in your region")
            
            # Get secure URL
            response = requests.get(api_url, headers=headers)
            self.logger.debug(f"API Response Status: {response.status_code}")
            self.logger.debug(f"API Response Headers: {dict(response.headers)}")
            
            try:
                mubi_data = response.json()
                self.logger.debug(f"API Response Data: {json.dumps(mubi_data, indent=2)}")
            except Exception as e:
                self.logger.error(f"Failed to parse API response: {e}")
                self.logger.debug(f"Raw Response: {response.text}")
                raise
                
            if response.status_code == 422:
                self.logger.error("Invalid credentials or session expired")
                raise ValueError("Your session has expired. Please log in again.")
            elif response.status_code != 200:
                error_msg = mubi_data.get('message', 'Unknown error')
                self.logger.error(f"API error: {error_msg}")
                raise ValueError(f"API returned {response.status_code}: {error_msg}")
            
            secure_url = mubi_data.get('url')
            if not secure_url:
                if 'errors' in mubi_data:
                    self.logger.error(f"API Errors: {mubi_data['errors']}")
                    raise ValueError(f"API Error: {mubi_data['errors']}")
                
                self.logger.error("No URL found in response")
                self.logger.debug(f"Available fields: {list(mubi_data.keys())}")
                raise ValueError("No streaming URL found in response. Your session may have expired.")
            
            # Get encryption key
            kid_response = requests.get(secure_url)
            kid_match = re.search(r'default_KID="([^"]+)"', str(kid_response.text))
            if not kid_match:
                raise ValueError("Failed to extract encryption key ID")
                
            kid = kid_match.group(1).replace('-', '')
            pssh = self._generate_pssh(kid)
            playready_pssh = self._extract_playready_pssh(secure_url)
            
            # Get decryption key
            dt_custom_data = headers.get('dt-custom-data')
            if not dt_custom_data:
                raise ValueError("Missing required dt-custom-data header")
            
            decryption_key = self._fetch_decryption_key(pssh, dt_custom_data, playready_pssh=playready_pssh)
            return decryption_key, secure_url
        except Exception as e:
            self.logger.error(f"Failed to get encryption info: {str(e)}")
            raise

    def _extract_playready_pssh(self, secure_url: str) -> Optional[str]:
        """Extract the PlayReady PSSH box from the video init segment.

        DRMtoday rejects cdmpool's Widevine device for Mubi, so the content
        keys are requested through PlayReady instead. The PlayReady PSSH is
        embedded in the video init segment referenced by the MPD.
        """
        try:
            manifest = requests.get(secure_url).text
            base_url = re.search(r'<BaseURL>([^<]+)</BaseURL>', manifest)
            init_template = re.search(r'<SegmentTemplate[^>]*initialization="([^"]+)"', manifest)
            rep_match = re.search(r'<Representation[^>]*id="video=[^"]*"[^>]*>', manifest)
            if not base_url or not init_template or not rep_match:
                self.logger.debug("Could not locate init segment template in MPD")
                return None
            rep_id = re.search(r'id="(video=[^"]+)"', rep_match.group(0)).group(1)
            init_url = secure_url.rsplit('/', 1)[0] + '/' + base_url.group(1).rstrip('/') + '/' + init_template.group(1).replace('$RepresentationID$', rep_id)
            self.logger.debug(f"Fetching init segment: {init_url}")
            init_data = requests.get(init_url).content

            # Find the pssh box (may be nested inside moov)
            for offset in range(len(init_data) - 7):
                if init_data[offset:offset + 4] == b'pssh':
                    size = int.from_bytes(init_data[offset - 4:offset], 'big')
                    box = init_data[offset - 4:offset - 4 + size]
                    system_id = box[12:28].hex()
                    if system_id == '9a04f07998404286ab92e65be0885f95':
                        self.logger.debug("Found PlayReady PSSH in init segment")
                        return base64.b64encode(box).decode('utf-8')
            self.logger.debug("No PlayReady PSSH found in init segment")
        except Exception as e:
            self.logger.debug(f"Failed to extract PlayReady PSSH: {e}")
        return None
    
    def _generate_pssh(self, key_id: str) -> str:
        """Generates PSSH box for decryption"""
        return build_pssh(key_id)
    
    def _fetch_decryption_key(self, pssh: str, dt_custom_data: str, playready_pssh: str = None) -> str:
        """Fetches decryption key via the cdmpool.xyz extraction API"""
        return fetch_decryption_key(pssh, dt_custom_data, playready_pssh=playready_pssh)
    
    def _prepare_headers(self) -> Dict:
        """Prepares headers for API requests"""
        try:
            auth_headers = self.auth_manager.generate_headers()
            # Use instance country code

            base_headers = {
                'authority': 'api.mubi.com',
                'accept': 'application/json',
                'accept-language': 'en-US,en;q=0.9',
                'client': 'web',
                'client-version': '1.0.0',
                'client-device': 'desktop',
                'client-device-info': 'Windows NT 10.0',
                'client-capabilities': 'drm-widevine',
                'client-accept-audio-codecs': 'aac',
                'client-accept-video-codecs': 'h265,vp9,h264',
                'origin': 'https://mubi.com',
                'referer': 'https://mubi.com/',
                'client-country': self.country_code,  # Already uppercase from __init__
                'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0.0.0',
                'sec-ch-ua': '"Not A(Brand";v="99", "Google Chrome";v="121", "Chromium";v="121"',
                'sec-ch-ua-mobile': '?0',
                'sec-ch-ua-platform': '"Windows"',
                'sec-fetch-dest': 'empty',
                'sec-fetch-mode': 'cors',
                'sec-fetch-site': 'same-site',
            }
            
            # Make sure auth headers exist and are properly formatted
            if 'dt-custom-data' in auth_headers:
                auth_headers['dt-custom-data'] = base64.b64encode(
                    json.dumps(json.loads(base64.b64decode(auth_headers['dt-custom-data']))).encode()
                ).decode()
                
            self.logger.debug(f"Using auth headers: {auth_headers}")
            return {**base_headers, **auth_headers}
        except Exception as e:
            self.logger.error(f"Failed to prepare headers: {str(e)}")
            raise
    
    def download_and_decrypt(self, movie_info: MovieInfo):
        """
        Downloads and decrypts the movie.
        
        Args:
            movie_info (MovieInfo): Information about the movie to download
        """
        try:
            api_url = f'https://api.mubi.com/v3/films/{movie_info.film_id}/viewing/secure_url'
            self.logger.debug(f"Fetching secure URL from: {api_url}")
            decryption_key, secure_url = self._get_encryption_info(api_url)
            self.logger.debug("Successfully obtained encryption info")
            
            # Download video
            dest_dir = os.path.join(self.download_folder, movie_info.full_title)
            os.makedirs(dest_dir, exist_ok=True)
            self.logger.debug(f"Created destination directory: {dest_dir}")
            
            self.logger.info("Downloading video...")
            download_cmd = f'N_m3u8DL-RE "{secure_url}" --auto-select --save-name "{movie_info.full_title}" ' \
                         f'--save-dir {self.download_folder} --tmp-dir {self.download_folder}/temp'
            self.logger.debug(f"Download command: {download_cmd}")
            os.system(download_cmd)
            
            input_file = f"{self.download_folder}/{movie_info.full_title}.mp4"
            output_file = f"{dest_dir}/decrypted-video.mp4"
            if not os.path.exists(input_file):
                self.logger.error(f"Downloaded file not found: {input_file}")
                raise FileNotFoundError(f"Downloaded file not found: {input_file}")
            
            # Decrypt video
            self.logger.info("Decrypting video...")
            decrypt_cmd = f'shaka-packager in="{input_file}",stream=video,output="{output_file}" ' \
                       f'--enable_raw_key_decryption --keys {decryption_key}'
            self.logger.debug(f"Decryption command: {decrypt_cmd}")
            os.system(decrypt_cmd)
            
            if not os.path.exists(output_file):
                self.logger.error(f"Decrypted file not found: {output_file}")
                raise FileNotFoundError(f"Decryption failed - output file not found: {output_file}")
                
            self.logger.debug("Video decryption completed successfully")
            
            # Process subtitles and audio
            self.logger.debug("Processing additional files...")
            self._process_additional_files(movie_info.full_title, dest_dir, decryption_key)
            self.logger.debug("Additional file processing completed")
            
        except Exception as e:
            self.logger.error(f"Download/decrypt failed: {e}")
            raise
    
    def _process_additional_files(self, title: str, dest_dir: str, decryption_key: str):
        """
        Processes subtitle and audio files
        
        Args:
            title (str): Movie title
            dest_dir (str): Destination directory for processed files
            decryption_key (str): Decryption key for audio files
        """
        self.logger.debug(f"Processing additional files for: {title}")
        self.logger.debug(f"Destination directory: {dest_dir}")
        
        regex_pattern = re.escape(title) + r"\.[a-z]{2,}\.m4a"
        self.logger.debug(f"Using audio file pattern: {regex_pattern}")
        
        processed_files = []
        
        for filename in os.listdir(self.download_folder):
            src_path = os.path.join(self.download_folder, filename)
            
            # Move subtitle files
            if filename.endswith(".srt") and title in filename:
                self.logger.debug(f"Found subtitle file: {filename}")
                dest_path = os.path.join(dest_dir, filename)
                try:
                    shutil.move(src_path, dest_path)
                    self.logger.debug(f"Moved subtitle file to: {dest_path}")
                    processed_files.append(dest_path)
                except Exception as e:
                    self.logger.error(f"Failed to move subtitle file: {str(e)}")
            
            # Process audio files
            if re.match(regex_pattern, filename):
                lang_match = re.search(re.escape(title) + r"\.([a-zA-Z]{2,})\.m4a", filename)
                if lang_match:
                    lang = lang_match.group(1)
                    self.logger.debug(f"Found audio track for language: {lang}")
                    
                    output_path = os.path.join(dest_dir, f"decrypted-audio.{lang}.m4a")
                    decrypt_cmd = f'shaka-packager in="{src_path}",' \
                                f'stream=audio,output="{output_path}" ' \
                                f'--enable_raw_key_decryption --keys {decryption_key}'
                    
                    self.logger.debug(f"Decrypting audio track: {lang}")
                    self.logger.debug(f"Decryption command: {decrypt_cmd}")
                    os.system(decrypt_cmd)
                    
                    if os.path.exists(output_path):
                        self.logger.debug(f"Successfully decrypted audio track: {lang}")
                        processed_files.append(output_path)
                        os.remove(src_path)
                        self.logger.debug(f"Removed encrypted audio file: {src_path}")
                    else:
                        self.logger.error(f"Failed to decrypt audio track: {lang}")
                    
        # Cleanup
        src_video = os.path.join(self.download_folder, f"{title}.mp4")
        if os.path.exists(src_video):
            self.logger.debug(f"Removing source video file: {src_video}")
            os.remove(src_video)
        
        self.logger.debug(f"Processed files: {processed_files}")

def setup_logging(debug: bool = False):
    """
    Configures logging for the application
    
    Args:
        debug (bool): Enable debug logging
    """
    level = logging.DEBUG if debug else logging.INFO
    log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    
    # Configure root logger
    logging.basicConfig(
        level=level,
        format=log_format,
        handlers=[
            logging.StreamHandler()
        ]
    )
    
    # Add file handler for all levels
    file_handler = logging.FileHandler('mubi_downloader.log')
    file_handler.setFormatter(logging.Formatter(log_format))
    file_handler.setLevel(level)
    logging.getLogger().addHandler(file_handler)
