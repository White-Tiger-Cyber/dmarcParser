MAGENTA = "\033[95m"
RESET = "\033[0m"
CYAN   = "\033[36m"
MAGENTA = "\033[35m"
GREEN  = "\033[32m"
YELLOW = "\033[33m"
RED    = "\033[31m"
BLUE   = "\033[34m"
WHITE  = "\033[37m"
ORANGE = "\033[38;5;208m"

BLACK_TEXT   = "\033[30m"       # standard black foreground
ORANGE_BG    = "\033[48;5;208m"

ascii_banner = r'''

/-\|/-\|/-\|/-\|/-\|/-\|/-\|/-\|/-\|/-\|/-\|/-\|/-\|/-\|/-\|/-\|/-\|/-\|/-\|/-\|/-\|/-\|/-\|/-\|/-\|/-\|/-\|/-\|/-\
|                                                                                                                 |
\                                                                                                                 /
-          ,,                                                                                                     -
/        `7MM                                            `7MM"""Mq.                                               \
|          MM                                              MM   `MM.                                              |
\     ,M""bMM  `7MMpMMMb.pMMMb.   ,6"Yb.  `7Mb,od8 ,p6"bo  MM   ,M9 ,6"Yb.  `7Mb,od8 ,pP"Ybd  .gP"Ya `7Mb,od8     /
-   ,AP    MM    MM    MM    MM  8)   MM    MM' "'6M'  OO  MMmmdM9 8)   MM    MM' "' 8I   `" ,M'   Yb  MM' "'     -
/   8MI    MM    MM    MM    MM   ,pm9MM    MM    8M       MM       ,pm9MM    MM     `YMMMa. 8M""""""  MM         \
|   `Mb    MM    MM    MM    MM  8M   MM    MM    YM.    , MM      8M   MM    MM     L.   I8 YM.    ,  MM         |
\    `Wbmd"MML..JMML  JMML  JMML.`Moo9^Yo..JMML.   YMbmd'.JMML.    `Moo9^Yo..JMML.   M9mmmP'  `Mbmmd'.JMML.       /
-                                                                                                                 -
/                                                                                                                 \
|                                                                                                                 |
\-/|\-/|\-/|\-/|\-/|\-/|\-/|\-/|\-/|\-/|\-/|\-/|\-/|\-/|\-/|\-/|\-/|\-/|\-/|\-/|\-/|\-/|\-/|\-/|\-/|\-/|\-/|\-/|\-/

'''


def dmarc_banner():
    print(f"{CYAN}{ascii_banner}{RESET}")
